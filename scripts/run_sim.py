"""
Connect an agent to a live RF Platform server.

Two modes:
  Simulator  — generates a synthetic pure tone (no file needed).
  Recording  — plays back a .sigmf-meta or .wav recording file.

Usage (from the agent/ directory):

    # Simulator
    python ../scripts/run_sim.py \\
        --server ws://localhost:8000/ws/agent \\
        --token  <token from tokens page>     \\
        --node-id <agent stable_node_id>

    # SigMF recording (center freq / sample rate read from file)
    python ../scripts/run_sim.py \\
        --server ws://localhost:8000/ws/agent \\
        --token  <token>  --node-id <id>      \\
        --file   /path/to/capture.sigmf-meta

    # WAV recording (center freq must be supplied; sample rate read from file)
    python ../scripts/run_sim.py \\
        --server ws://localhost:8000/ws/agent \\
        --token  <token>  --node-id <id>      \\
        --file   /path/to/capture.wav  --freq 433920000

Low-FPS simulator examples:
    ~10 fps  --sample-rate 10240  --fft-size 1024  --rate-limit-msps 0.01024
    ~5 fps   --sample-rate  5120  --fft-size 1024  --rate-limit-msps 0.00512

Real-time recording playback (set rate-limit-msps = sample_rate / 1e6):
    e.g. 2.048 Msps file  →  --rate-limit-msps 2.048

FPS = sample_rate_hz / fft_size
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent" / "src"))

from agent.config import (
    AgentConfig,
    AgentIdentity,
    QueueConfig,
    ReconnectConfig,
    ServerConfig,
    TelemetryConfig,
)
from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
)
from agent.processing.processor import IQProcessor
from agent.protocol import JsonBase64Codec
from agent.session import Session
from agent.source.sigmf import SigMFSource, _DATATYPE_MAP
from agent.source.wav import WavSource, _parse_wav_header, _HEADER_READ_SIZE
from agent.telemetry import MetricsCollector
from agent.telemetry.loop import TelemetryLoop
from agent.telemetry.stage_timing import PipelineTiming
from agent.transport.transport import WebSocketTransport


# ---------------------------------------------------------------------------
# Simulator source
# ---------------------------------------------------------------------------


class SimulatorSource:
    """Generates synthetic float32 IQ blocks — no hardware required."""

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
        n_samples = self._block_size // 8

        while True:
            floats: list[float] = []
            for _ in range(n_samples):
                floats.append(math.cos(self._phase))
                floats.append(math.sin(self._phase))
                self._phase = (self._phase + omega) % (2.0 * math.pi)
            await output.put(struct.pack(f"<{len(floats)}f", *floats))
            await asyncio.sleep(self._sleep_per_block or 0)


# ---------------------------------------------------------------------------
# File metadata helpers
# ---------------------------------------------------------------------------


def _iq_from_sigmf(meta_path: Path) -> IQDescriptor:
    """Read .sigmf-meta and return an IQDescriptor (synchronous)."""
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    g = meta["global"]
    datatype: str = g["core:datatype"]
    if datatype not in _DATATYPE_MAP:
        raise SystemExit(
            f"Unsupported SigMF datatype {datatype!r}. "
            f"Supported: {sorted(_DATATYPE_MAP)}"
        )
    sample_format, endianness = _DATATYPE_MAP[datatype]
    captures = meta.get("captures", [])
    if not captures:
        raise SystemExit("sigmf-meta has no captures entries")
    return IQDescriptor(
        sample_format=sample_format,
        endianness=endianness,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=int(g["core:sample_rate"]),
        center_freq_hz=int(captures[0]["core:frequency"]),
    )


def _iq_from_wav(wav_path: Path, center_freq_hz: int) -> IQDescriptor:
    """Read WAV header and return an IQDescriptor (synchronous)."""
    with wav_path.open("rb") as f:
        header_data = f.read(_HEADER_READ_SIZE)
    sample_format, sample_rate_hz, _ = _parse_wav_header(header_data)
    return IQDescriptor(
        sample_format=sample_format,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=sample_rate_hz,
        center_freq_hz=center_freq_hz,
    )


# ---------------------------------------------------------------------------
# Telemetry sender adapter
# ---------------------------------------------------------------------------


class _TransportSender:
    def __init__(self, transport: WebSocketTransport, codec: JsonBase64Codec) -> None:
        self._transport = transport
        self._codec = codec

    async def send_heartbeat(self, node_id: str, session_id: str, timestamp_utc: str) -> None:
        await self._transport.send(
            self._codec.encode_heartbeat(node_id, session_id, timestamp_utc)
        )

    async def send_agent_status(self, node_id: str, session_id: str, timestamp_utc: str, metrics) -> None:
        await self._transport.send(
            self._codec.encode_agent_status(node_id, session_id, timestamp_utc, metrics)
        )


# ---------------------------------------------------------------------------
# Factory wiring
# ---------------------------------------------------------------------------


def _make_factories(
    transport: WebSocketTransport,
    codec: JsonBase64Codec,
    source_factory,
    pipeline_timing: PipelineTiming,
    shared_metrics: MetricsCollector,
):
    from agent.app.runner import RunnerFactories

    def make_transport(cfg: AgentConfig) -> WebSocketTransport:
        return transport

    def make_codec(cfg: AgentConfig) -> JsonBase64Codec:
        return codec

    def make_processor(cfg: AgentConfig) -> IQProcessor:
        return IQProcessor(
            descriptor=cfg.iq,
            rf_config=cfg.rf,
            timings=pipeline_timing,
            metrics=shared_metrics,
        )

    def make_session(cfg: AgentConfig, t: WebSocketTransport, c: JsonBase64Codec) -> Session:
        return Session(
            config=cfg,
            transport=t,
            codec=c,
            timings=pipeline_timing,
            metrics=shared_metrics,
        )

    def make_telemetry(cfg, session, metrics, t, c) -> TelemetryLoop:
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
        make_source=source_factory,
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
    file_path = Path(args.file) if args.file else None

    # --- Resolve IQDescriptor and source factory from file or simulator args ---
    if file_path is not None:
        suffix = file_path.suffix.lower()
        if suffix == ".sigmf-data":
            file_path = file_path.with_suffix(".sigmf-meta")
            suffix = ".sigmf-meta"
        if suffix == ".sigmf-meta":
            iq = _iq_from_sigmf(file_path)
            source_label = f"sigmf:{file_path.name}"
            # One block = one FFT frame worth of samples → smooth per-frame pacing.
            file_block_size = args.fft_size * iq.sample_format.bytes_per_sample

            def make_source(cfg: AgentConfig) -> SigMFSource:
                return SigMFSource(
                    meta_path=file_path,
                    block_size=file_block_size,
                    loops=None,
                    rate_limit_msps=args.rate_limit_msps,
                )

        elif suffix == ".wav":
            if args.freq is None:
                raise SystemExit("--freq is required for WAV files (WAV has no center frequency metadata)")
            iq = _iq_from_wav(file_path, args.freq)
            source_label = f"wav:{file_path.name}"
            file_block_size = args.fft_size * iq.sample_format.bytes_per_sample

            def make_source(cfg: AgentConfig) -> WavSource:
                return WavSource(
                    wav_path=file_path,
                    center_freq_hz=cfg.iq.center_freq_hz,
                    block_size=file_block_size,
                    loops=None,
                    rate_limit_msps=args.rate_limit_msps,
                )

        else:
            raise SystemExit(f"Unsupported file type {suffix!r}. Use .sigmf-meta or .wav")

    else:
        iq = IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=args.sample_rate,
            center_freq_hz=args.freq or 433_920_000,
        )
        source_label = "simulator"

        def make_source(cfg: AgentConfig) -> SimulatorSource:
            return SimulatorSource(descriptor=cfg.iq, rate_limit_msps=args.rate_limit_msps)

    if args.fps is not None and args.rate_limit_msps is not None:
        raise SystemExit("--fps and --rate-limit-msps are mutually exclusive")
    if args.fps is not None:
        rate_limit_msps: float | None = args.fps * args.fft_size / 1e6
    else:
        rate_limit_msps = args.rate_limit_msps

    # Patch rate_limit_msps into closures that captured args.rate_limit_msps
    args.rate_limit_msps = rate_limit_msps

    rf = RFConfig(
        center_freq_hz=iq.center_freq_hz,
        sample_rate_hz=iq.sample_rate_hz,
        fft_size=args.fft_size,
    )
    cfg = AgentConfig(
        identity=AgentIdentity(node_id=args.node_id),
        server=ServerConfig(url=args.server, token=args.token),
        rf=rf,
        iq=iq,
        queues=QueueConfig(iq_queue_size=8, frame_queue_size=16),
        telemetry=TelemetryConfig(heartbeat_interval_s=5.0, status_interval_s=10.0),
        reconnect=ReconnectConfig(
            initial_delay_s=2.0, max_delay_s=30.0, backoff_factor=2.0, jitter=True
        ),
    )

    logical_fps = iq.sample_rate_hz / args.fft_size
    effective_fps = (rate_limit_msps * 1e6 / args.fft_size) if rate_limit_msps else logical_fps
    print(
        f"source={source_label}  node_id={args.node_id}  server={args.server}\n"
        f"  center={iq.center_freq_hz / 1e6:.3f} MHz  "
        f"sample_rate={iq.sample_rate_hz / 1e6:.3f} Msps  fft_size={args.fft_size}\n"
        f"  logical fps={logical_fps:.1f}  effective fps={effective_fps:.1f}  "
        f"rate_limit={rate_limit_msps or 'unlimited'} Msps\n"
        "Press Ctrl-C to stop."
    )

    transport = WebSocketTransport()
    codec = JsonBase64Codec()
    pipeline_timing = PipelineTiming()
    shared_metrics = MetricsCollector(timings=pipeline_timing)
    factories = _make_factories(transport, codec, make_source, pipeline_timing, shared_metrics)

    from agent.app.runner import AgentRunner

    runner = AgentRunner(config=cfg, factories=factories)
    await runner.run_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent → live server (simulator or recording)")
    parser.add_argument("--server",   required=True, help="WebSocket URL, e.g. ws://localhost:8000/ws/agent")
    parser.add_argument("--token",    required=True, help="Raw agent bearer token")
    parser.add_argument("--node-id",  required=True, dest="node_id", help="Agent stable_node_id")
    parser.add_argument("--file",     default=None,
                        help="Recording file (.sigmf-meta or .wav). Omit to use the synthetic simulator.")
    parser.add_argument("--fft-size", type=int, default=1024, dest="fft_size")
    parser.add_argument("--sample-rate", type=int, default=240_000, dest="sample_rate",
                        help="IQ sample rate Hz — simulator only; ignored when --file is given.")
    parser.add_argument("--freq",     type=int, default=None,
                        help="Centre frequency Hz — simulator default 433920000; "
                             "required for .wav files; ignored for .sigmf-meta files.")
    parser.add_argument("--fps", type=float, default=None,
                        help="Target output frames per second. Computes rate-limit-msps automatically. "
                             "Mutually exclusive with --rate-limit-msps.")
    parser.add_argument("--rate-limit-msps", type=float, default=None, dest="rate_limit_msps",
                        metavar="MSPS",
                        help="Throttle IQ output to N Msps. "
                             "Set to sample_rate/1e6 for real-time pacing. Default: unlimited.")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
