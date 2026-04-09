"""
Live demo: agent → fake server with JSON stats.

Runs the full agent stack (source → processor → session → telemetry)
against a local fake server and prints rolling JSON metrics every second.

Usage (from repo root):

    cd agent

    # Synthetic source (no file needed)
    python ../scripts/run_demo.py --fft-size 4096 --duration 10

    # Real SigMF recording (loops automatically)
    python ../scripts/run_demo.py --sigmf ../recordings/LTE_uplink.../....sigmf-meta --fft-size 4096

    # WAV recording (loops automatically; --freq required)
    python ../scripts/run_demo.py --wav ../recordings/UMTS.wav --freq 882400000 --fft-size 1024

Options:
    --fft-size          FFT size (default 1024)
    --sample-rate       IQ sample rate Hz — ignored when --sigmf or --wav is set
    --freq              Center frequency Hz — ignored when --sigmf is set; required for --wav
    --stats-hz          Stats print rate in Hz (default 1.0)
    --duration          Run for N seconds then stop (default: run forever)
    --rate-limit-msps   Throttle synthetic source to N MSPS (default: unlimited)
    --sigmf             Path to a .sigmf-meta file — uses real recording as source
    --wav               Path to a .wav IQ file — uses real recording as source (--freq required)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import math
import os
import random
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---- Make scripts/ importable from agent/ working directory ----------------
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ---- Agent imports ---------------------------------------------------------
# Run this script from the agent/ directory so the agent package is on path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent" / "src"))

from agent.config import (
    AgentConfig,
    AgentIdentity,
    BandwidthConfig,
    QueueConfig,
    ReconnectConfig,
    ServerConfig,
    TelemetryConfig,
)
from agent.domain import (
    ConnectionState,
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    SpectrumFrame,
    WireEncoding,
)
from agent.processing.processor import IQProcessor
from agent.protocol import JsonBase64Codec
from agent.session import Session
from agent.source.sigmf import SigMFSource
from agent.source.wav import WavSource
from agent.telemetry import MetricsCollector
from agent.telemetry.loop import TelemetryLoop, TelemetrySender
from agent.telemetry.stage_timing import PipelineTiming
from agent.transport.transport import WebSocketTransport
from fake_server import ConnectionRecord, FakeAgentServer, FakeServerConfig


# ---------------------------------------------------------------------------
# Synthetic IQ source
# ---------------------------------------------------------------------------


class SimulatorSource:
    """Generates synthetic float32 IQ blocks without any hardware.

    Produces a pure tone at tone_offset_hz above the centre frequency so
    the FFT peak lands in a known bin (useful for visual sanity checks).
    """

    def __init__(
        self,
        descriptor: IQDescriptor,
        block_size: int = 8_192,
        tone_offset_hz: float = 100_000.0,
        rate_limit_msps: float | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._block_size = block_size
        self._tone_offset_hz = tone_offset_hz
        self._phase = 0.0
        # Seconds to sleep after each block to hit the target IQ rate.
        # None = unlimited (run as fast as the pipeline allows).
        self._sleep_per_block: float | None = None
        if rate_limit_msps is not None:
            samples_per_block = block_size // 8  # float32 complex = 8 bytes
            self._sleep_per_block = samples_per_block / (rate_limit_msps * 1e6)

    @property
    def descriptor(self) -> IQDescriptor:
        return self._descriptor

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        sr = self._descriptor.sample_rate_hz
        omega = 2.0 * math.pi * self._tone_offset_hz / sr
        n_samples = self._block_size // 8  # 8 bytes per complex float32 sample

        while True:
            floats: list[float] = []
            for _ in range(n_samples):
                i = math.cos(self._phase)
                q = math.sin(self._phase)
                floats.append(i)
                floats.append(q)
                self._phase = (self._phase + omega) % (2.0 * math.pi)

            chunk = struct.pack(f"<{len(floats)}f", *floats)
            await output.put(chunk)
            # Rate limiting: sleep the exact wall-clock time that this block
            # would take to arrive from a real SDR at the configured MSPS.
            # sleep_per_block=None means unlimited — run as fast as possible.
            await asyncio.sleep(self._sleep_per_block or 0)


# ---------------------------------------------------------------------------
# Minimal TelemetrySender (sends over transport using codec)
# ---------------------------------------------------------------------------


class _TransportSender:
    """Adapts Transport + ProtocolCodec to TelemetrySender protocol."""

    def __init__(self, transport: WebSocketTransport, codec: JsonBase64Codec) -> None:
        self._transport = transport
        self._codec = codec

    async def send_heartbeat(
        self, node_id: str, session_id: str, timestamp_utc: str
    ) -> None:
        msg = self._codec.encode_heartbeat(node_id, session_id, timestamp_utc)
        await self._transport.send(msg)

    async def send_agent_status(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
        metrics: Any,
    ) -> None:
        msg = self._codec.encode_agent_status(
            node_id, session_id, timestamp_utc, metrics
        )
        await self._transport.send(msg)


# ---------------------------------------------------------------------------
# Stats printer (server-side view)
# ---------------------------------------------------------------------------


@dataclass
class _Snapshot:
    elapsed_s: float
    total_frames: int
    fps: float
    iq_msps: float        # IQ mega-samples/sec (fps × fft_size / 1e6)
    iq_mb_s: float        # IQ throughput in MB/s (float32 = 8 bytes/complex sample)
    rx_bytes_per_sec: float   # actual WebSocket bytes/sec received by server
    total_heartbeats: int
    total_statuses: int
    last_status: dict[str, Any] | None


def _build_snapshot(
    record: ConnectionRecord | None,
    start_time: float,
    prev_frames: int,
    prev_bytes: int,
    prev_time: float,
) -> tuple[_Snapshot, int, int, float]:
    now = time.monotonic()
    elapsed = now - start_time

    frames = record.frame_count if record else 0
    rx_bytes = record.rx_bytes if record else 0
    hb = len(record.heartbeats) if record else 0
    statuses = len(record.statuses) if record else 0

    dt = now - prev_time
    fps = (frames - prev_frames) / dt if dt > 0 else 0.0
    rx_bytes_per_sec = (rx_bytes - prev_bytes) / dt if dt > 0 else 0.0

    # IQ samples consumed = FFT frames × fft_size (one frame needs fft_size complex samples)
    fft_size = 1024  # placeholder; overwritten in _print_stats with real config
    iq_msps = fps * fft_size / 1e6
    iq_mb_s = iq_msps * 8  # float32 complex = 8 bytes per sample

    last_status: dict[str, Any] | None = None
    if record and record.statuses:
        last_status = record.statuses[-1]

    snap = _Snapshot(
        elapsed_s=elapsed,
        total_frames=frames,
        fps=fps,
        iq_msps=iq_msps,
        iq_mb_s=iq_mb_s,
        rx_bytes_per_sec=rx_bytes_per_sec,
        total_heartbeats=hb,
        total_statuses=statuses,
        last_status=last_status,
    )
    return snap, frames, rx_bytes, now


def _print_stats(snap: _Snapshot, config: AgentConfig) -> None:
    # Recompute IQ figures with the real fft_size (snapshot used a placeholder).
    iq_msps = snap.fps * config.rf.fft_size / 1e6
    iq_mb_s = iq_msps * 8  # float32 complex = 8 bytes per sample

    # Real-time capacity: how many fps are needed to keep up with the SDR rate,
    # and what fraction of that we're actually achieving.
    realtime_fps = config.rf.sample_rate_hz / config.rf.fft_size
    realtime_ratio = snap.fps / realtime_fps if realtime_fps > 0 else 0.0

    doc: dict[str, Any] = {
        "time_s": round(snap.elapsed_s, 1),
        "frames_received": snap.total_frames,
        "fps": round(snap.fps, 2),
        "realtime_fps_needed": round(realtime_fps, 1),
        "realtime_ratio": round(realtime_ratio, 3),  # 1.0 = keeping up, <1.0 = falling behind
        "can_keep_up": realtime_ratio >= 1.0,
        "ws_bytes_per_sec": round(snap.rx_bytes_per_sec),
        "ws_kb_per_sec": round(snap.rx_bytes_per_sec / 1024, 1),
        "ws_mb_per_sec": round(snap.rx_bytes_per_sec / 1_048_576, 2),
        "iq_msps": round(iq_msps, 3),
        "iq_mb_s": round(iq_mb_s, 2),
        "fft_size": config.rf.fft_size,
        "center_freq_mhz": config.rf.center_freq_hz / 1e6,
        "sample_rate_msps": config.rf.sample_rate_hz / 1e6,
        "heartbeats": snap.total_heartbeats,
        "agent_status_msgs": snap.total_statuses,
    }

    if snap.last_status:
        st = snap.last_status
        doc["agent"] = {
            "cpu_pct": st.get("cpu_usage_pct"),
            "throttled": st.get("throttled"),
            "tx_bytes_s": st.get("tx_bytes_per_sec"),
            "queue_depth": st.get("queue_depth"),
            "queue_fill_pct": st.get("queue_fill_pct"),
            "drops": st.get("drops"),
        }
        if "pipeline" in st:
            p = st["pipeline"]
            doc["pipeline_ms"] = {
                "parse_iq":  {"p50": p.get("parse_iq_p50_ms"),  "p99": p.get("parse_iq_p99_ms")},
                "fft":       {"p50": p.get("fft_p50_ms"),        "p99": p.get("fft_p99_ms")},
                "encode_send": {"p50": p.get("encode_send_p50_ms"), "p99": p.get("encode_send_p99_ms")},
                "iq_queue_depth_avg":    p.get("iq_queue_depth_avg"),
                "frame_queue_depth_avg": p.get("frame_queue_depth_avg"),
            }

    print(json.dumps(doc), flush=True)


# ---------------------------------------------------------------------------
# Stats loop (runs as an asyncio task alongside the agent)
# ---------------------------------------------------------------------------


async def _cpu_updater(metrics: MetricsCollector, interval_s: float = 1.0) -> None:
    """Measures process CPU usage and feeds it into MetricsCollector."""
    prev_pt = time.process_time()
    prev_wt = time.monotonic()
    while True:
        await asyncio.sleep(interval_s)
        cur_pt = time.process_time()
        cur_wt = time.monotonic()
        wall_dt = cur_wt - prev_wt
        if wall_dt > 0:
            cpu_pct = (cur_pt - prev_pt) / wall_dt * 100.0
            metrics.set_cpu_usage_pct(min(cpu_pct, 100.0))
        prev_pt = cur_pt
        prev_wt = cur_wt


async def _stats_loop(
    server: FakeAgentServer,
    config: AgentConfig,
    interval_s: float,
    duration_s: float | None,
) -> None:
    start = time.monotonic()
    prev_frames = 0
    prev_bytes = 0
    prev_time = start

    while True:
        await asyncio.sleep(interval_s)

        record = server.connections[0] if server.connections else None
        snap, prev_frames, prev_bytes, prev_time = _build_snapshot(
            record, start, prev_frames, prev_bytes, prev_time
        )
        _print_stats(snap, config)

        if duration_s is not None and snap.elapsed_s >= duration_s:
            return


# ---------------------------------------------------------------------------
# Agent wiring
# ---------------------------------------------------------------------------


def _make_factories(
    transport: WebSocketTransport,
    codec: JsonBase64Codec,
    rate_limit_msps: float | None,
    shared_metrics: MetricsCollector,
    pipeline_timing: PipelineTiming,
    sigmf_source: SigMFSource | None = None,
    wav_source: WavSource | None = None,
):
    """Return RunnerFactories that use the pre-built transport and codec."""
    from agent.app.runner import RunnerFactories

    def make_transport(cfg: AgentConfig) -> WebSocketTransport:
        return transport

    def make_codec(cfg: AgentConfig) -> JsonBase64Codec:
        return codec

    def make_source(cfg: AgentConfig) -> SigMFSource | WavSource | SimulatorSource:
        if sigmf_source is not None:
            return sigmf_source
        if wav_source is not None:
            return wav_source
        return SimulatorSource(descriptor=cfg.iq, rate_limit_msps=rate_limit_msps)

    def make_processor(cfg: AgentConfig) -> IQProcessor:
        return IQProcessor(
            descriptor=cfg.iq,
            rf_config=cfg.rf,
            timings=pipeline_timing,
            metrics=shared_metrics,
        )

    def make_session(
        cfg: AgentConfig, t: WebSocketTransport, c: JsonBase64Codec
    ) -> Session:
        return Session(
            config=cfg,
            transport=t,
            codec=c,
            timings=pipeline_timing,
            metrics=shared_metrics,
        )

    def make_telemetry(
        cfg: AgentConfig,
        session: Session,
        metrics: MetricsCollector,
        t: WebSocketTransport,
        c: JsonBase64Codec,
    ) -> TelemetryLoop:
        # Use the shared instance so the CPU updater coroutine can feed it.
        return TelemetryLoop(
            node_id=cfg.identity.node_id,
            session=session,
            sender=_TransportSender(t, c),
            metrics=shared_metrics,
            heartbeat_interval_sec=cfg.telemetry.heartbeat_interval_s,
            status_interval_sec=cfg.telemetry.status_interval_s,
            clock=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
            sleep=asyncio.sleep,
        )

    return RunnerFactories(
        make_source=make_source,
        make_processor=make_processor,
        make_transport=make_transport,
        make_codec=make_codec,
        make_session=make_session,
        make_telemetry=make_telemetry,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> None:
    # --- Source selection ---------------------------------------------------
    sigmf_source: SigMFSource | None = None
    wav_source: WavSource | None = None

    if args.sigmf:
        meta_path = Path(args.sigmf).resolve()
        if not meta_path.exists():
            print(json.dumps({"event": "error", "msg": f"not found: {meta_path}"}))
            return
        # loops=None → replay the file indefinitely
        sigmf_source = SigMFSource(meta_path, loops=None)
        await sigmf_source.start()          # parse meta → builds descriptor
        iq = sigmf_source.descriptor
        rf = RFConfig(
            center_freq_hz=iq.center_freq_hz,
            sample_rate_hz=iq.sample_rate_hz,
            fft_size=args.fft_size,
        )
        source_label = meta_path.name
    elif args.wav:
        wav_path = Path(args.wav).resolve()
        if not wav_path.exists():
            print(json.dumps({"event": "error", "msg": f"not found: {wav_path}"}))
            return
        if args.freq is None:
            print(json.dumps({"event": "error", "msg": "--freq is required with --wav"}))
            return
        # loops=None → replay the file indefinitely
        wav_source = WavSource(
            wav_path,
            center_freq_hz=args.freq,
            loops=None,
            rate_limit_msps=args.rate_limit_msps,
        )
        await wav_source.start()            # parse WAV header → builds descriptor
        iq = wav_source.descriptor
        rf = RFConfig(
            center_freq_hz=iq.center_freq_hz,
            sample_rate_hz=iq.sample_rate_hz,
            fft_size=args.fft_size,
        )
        source_label = wav_path.name
    else:
        sim_freq = args.freq if args.freq is not None else 433_920_000
        iq = IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=args.sample_rate,
            center_freq_hz=sim_freq,
        )
        rf = RFConfig(
            center_freq_hz=sim_freq,
            sample_rate_hz=args.sample_rate,
            fft_size=args.fft_size,
        )
        source_label = "simulator"

    server_cfg = FakeServerConfig(
        host="127.0.0.1",
        port=0,
        disconnect_after_n_frames=None,  # run indefinitely
        store_frames=False,  # don't accumulate frame dicts — use frame_count instead
    )

    async with FakeAgentServer(server_cfg) as server:
        wire_encoding = WireEncoding(args.encoding)
        agent_cfg = AgentConfig(
            identity=AgentIdentity(node_id="demo-agent"),
            server=ServerConfig(url=server.ws_url, token="demo-token"),
            rf=rf,
            iq=iq,
            wire_encoding=wire_encoding,
            queues=QueueConfig(iq_queue_size=8, frame_queue_size=16),
            telemetry=TelemetryConfig(
                heartbeat_interval_s=5.0,
                status_interval_s=3.0,
            ),
            reconnect=ReconnectConfig(
                initial_delay_s=1.0,
                max_delay_s=10.0,
                backoff_factor=2.0,
                jitter=False,
            ),
            bandwidth=BandwidthConfig(
                max_bytes_per_sec=(
                    int(args.max_bw_kbps * 1000) if args.max_bw_kbps is not None else None
                ),
                strategy=args.bw_strategy,
            ),
        )

        transport = WebSocketTransport()
        codec = JsonBase64Codec()
        pipeline_timing = PipelineTiming()
        shared_metrics = MetricsCollector(timings=pipeline_timing)
        factories = _make_factories(
            transport, codec, args.rate_limit_msps, shared_metrics, pipeline_timing,
            sigmf_source, wav_source,
        )

        from agent.app.runner import AgentRunner

        runner = AgentRunner(config=agent_cfg, factories=factories)

        print(
            json.dumps(
                {
                    "event": "start",
                    "source": source_label,
                    "server": server.ws_url,
                    "fft_size": rf.fft_size,
                    "sample_rate_msps": rf.sample_rate_hz / 1e6,
                    "center_freq_mhz": rf.center_freq_hz / 1e6,
                    "iq_format": iq.sample_format.value,
                    "wire_encoding": args.encoding,
                    "rate_limit_msps": args.rate_limit_msps,
                }
            ),
            flush=True,
        )

        stats_task = asyncio.create_task(
            _stats_loop(server, agent_cfg, 1.0 / args.stats_hz, args.duration)
        )
        cpu_task = asyncio.create_task(_cpu_updater(shared_metrics, interval_s=1.0))
        agent_task = asyncio.create_task(runner.run_once())

        done, pending = await asyncio.wait(
            [stats_task, agent_task, cpu_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        # Final snapshot
        record = server.connections[0] if server.connections else None
        snap, _, _, _ = _build_snapshot(record, time.monotonic(), 0, 0, time.monotonic())
        print(
            json.dumps(
                {
                    "event": "done",
                    "total_frames": record.frame_count if record else 0,
                    "total_heartbeats": len(record.heartbeats) if record else 0,
                    "total_statuses": len(record.statuses) if record else 0,
                }
            ),
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="RF agent live demo")
    parser.add_argument("--fft-size", type=int, default=1024)
    parser.add_argument("--sample-rate", type=int, default=2_400_000)
    parser.add_argument(
        "--encoding",
        choices=["json_base64", "binary_ws"],
        default="json_base64",
        help="Wire encoding for spectrum frames (default: json_base64)",
    )
    parser.add_argument("--freq", type=int, default=None, help="Center frequency Hz (required for --wav; defaults to 433920000 for simulator)")
    parser.add_argument("--stats-hz", type=float, default=1.0)
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Stop after N seconds (default: run until Ctrl-C)",
    )
    parser.add_argument(
        "--rate-limit-msps",
        type=float,
        default=None,
        metavar="MSPS",
        help=(
            "Throttle IQ output to this many mega-samples/sec. "
            "Applies to the synthetic simulator and WAV sources. "
            "Use the source's native sample rate (e.g. 1.25 for MWlamp WAV) "
            "to benchmark at real-hardware speed. "
            "Default: unlimited — run as fast as the pipeline allows."
        ),
    )
    parser.add_argument(
        "--max-bw-kbps",
        type=float,
        default=None,
        metavar="KBPS",
        help=(
            "Cap outbound WebSocket bandwidth to N kilobytes/sec. "
            "Frames that exceed the budget are dropped and counted in "
            "local_throttle. Default: unlimited."
        ),
    )
    parser.add_argument(
        "--bw-strategy",
        choices=["decimate", "drop"],
        default="decimate",
        help=(
            "How to handle frames when the bandwidth cap is reached. "
            "'decimate' (default): space sent frames evenly over time. "
            "'drop': send greedily until the byte budget is exhausted."
        ),
    )
    parser.add_argument(
        "--sigmf",
        default=None,
        metavar="META_PATH",
        help=(
            "Path to a .sigmf-meta file. Uses the real recording as source "
            "(loops indefinitely). Overrides --sample-rate and --freq."
        ),
    )
    parser.add_argument(
        "--wav",
        default=None,
        metavar="WAV_PATH",
        help=(
            "Path to a .wav IQ file (stereo, I=left, Q=right). Uses the real "
            "recording as source (loops indefinitely). Requires --freq. "
            "Overrides --sample-rate."
        ),
    )
    args = parser.parse_args()

    async def _main() -> None:
        # Suppress "Task exception was never retrieved" for expected shutdown
        # errors (closed WebSocket after the stats window ends).
        loop = asyncio.get_running_loop()

        def _exc_handler(loop: asyncio.AbstractEventLoop, ctx: dict) -> None:
            exc = ctx.get("exception")
            msg = ctx.get("message", "")
            if isinstance(exc, (ConnectionError, asyncio.CancelledError)):
                return
            if "Task exception was never retrieved" in msg:
                return
            loop.default_exception_handler(ctx)

        loop.set_exception_handler(_exc_handler)
        await run(args)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print(json.dumps({"event": "interrupted"}), flush=True)


if __name__ == "__main__":
    main()
