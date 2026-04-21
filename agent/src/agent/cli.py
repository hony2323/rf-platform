"""CLI entry point for the RF agent.

Commands
--------
rf-agent connect [OPTIONS]   Connect to the server and start streaming.

Config file (TOML) is auto-discovered at:
  ./rf-agent.toml
  ~/.rf-agent/config.toml

CLI flags override config file values. Both override environment variables.

Environment variables
---------------------
RF_AGENT_TOKEN   bearer token  (config: server.token)
RF_AGENT_SERVER  server URL    (config: server.url)

Example rf-agent.toml
---------------------
[server]
url = "ws://localhost:8000/ws/agent"
# token = "..."  # prefer --token flag or RF_AGENT_TOKEN

[identity]
node_id = "my-sdr"

[source]
# file = "/recordings/capture.sigmf-meta"  # omit for simulator
fps = 10
fft_size = 1024
sample_rate = 240000   # simulator only
# freq = 433920000     # simulator default; required for .wav

[reconnect]
initial_delay_s = 2.0
max_delay_s = 30.0
backoff_factor = 2.0
jitter = true
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

from agent.app.factories import make_standard_factories
from agent.app.runner import AgentRunner, BuildFailure
from agent.config import (
    AgentConfig,
    AgentIdentity,
    ReconnectConfig,
    ServerConfig,
)
from agent.domain import Endianness, IQDescriptor, Layout, RFConfig, SampleFormat
from agent.source.sigmf import SigMFSource
from agent.source.sigmf import read_iq_descriptor as sigmf_read_iq
from agent.source.simulator import SimulatorSource
from agent.source.wav import WavSource
from agent.source.wav import read_iq_descriptor as wav_read_iq

_VERSION = "0.3.0"
_DEFAULT_CONFIG_PATHS = [
    Path("rf-agent.toml"),
    Path.home() / ".rf-agent" / "config.toml",
]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config_file(explicit: Path | None) -> dict[str, Any]:
    """Load TOML config.
    Returns {} if no file found and none was explicitly requested."""
    path: Path | None = explicit
    if path is None:
        for candidate in _DEFAULT_CONFIG_PATHS:
            if candidate.is_file():
                path = candidate
                break
        else:
            return {}

    if not path.is_file():
        raise SystemExit(f"Config file not found: {path}")

    if tomllib is None:
        raise SystemExit(
            "Cannot read config file: install 'tomli' (pip install tomli) "
            "or upgrade to Python 3.11+."
        )

    with path.open("rb") as f:
        data = tomllib.load(f)
    print(f"Using config: {path}")
    return data  # type: ignore[return-value]


def _get(d: dict[str, Any], *keys: str) -> Any:
    obj: Any = d
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


def _pick(*candidates: Any) -> Any:
    """Return the first non-None value."""
    for c in candidates:
        if c is not None:
            return c
    return None


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rf-agent",
        description="RF Platform spectrum streaming agent",
    )
    p.add_argument("--version", action="version", version=f"rf-agent {_VERSION}")

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    conn = sub.add_parser(
        "connect",
        help="Connect to the RF Platform server and start streaming",
        description=(
            "Connect to the RF Platform server and start streaming spectrum data. "
            "Settings are loaded from rf-agent.toml (or ~/.rf-agent/config.toml) "
            "and can be overridden with flags."
        ),
    )

    conn.add_argument(
        "--config",
        metavar="FILE",
        default=None,
        help="Config file path (TOML). "
        "Default: ./rf-agent.toml or ~/.rf-agent/config.toml",
    )

    g = conn.add_argument_group("connection")
    g.add_argument(
        "--server",
        metavar="URL",
        default=None,
        help="WebSocket server URL  (env: RF_AGENT_SERVER; config: server.url)",
    )
    g.add_argument(
        "--token",
        metavar="TOKEN",
        default=None,
        help="Bearer token  (env: RF_AGENT_TOKEN; config: server.token)",
    )
    g.add_argument(
        "--node-id",
        metavar="ID",
        dest="node_id",
        default=None,
        help="Stable node identifier  (config: identity.node_id)",
    )

    g = conn.add_argument_group("source")
    g.add_argument(
        "--file",
        metavar="PATH",
        default=None,
        help="Recording file (.sigmf-meta, .sigmf-data, or .wav). "
        "Omit to use the built-in simulator.  (config: source.file)",
    )
    g.add_argument(
        "--fps",
        type=float,
        metavar="N",
        default=None,
        help="Target frames per second  (config: source.fps). "
        "Mutually exclusive with --rate-limit-msps.",
    )
    g.add_argument(
        "--rate-limit-msps",
        type=float,
        dest="rate_limit_msps",
        metavar="MSPS",
        default=None,
        help="Throttle IQ output to N Msps. Mutually exclusive with --fps.",
    )
    g.add_argument(
        "--fft-size",
        type=int,
        dest="fft_size",
        metavar="N",
        default=None,
        help="FFT window size in samples  (config: source.fft_size; default: 1024)",
    )
    g.add_argument(
        "--sample-rate",
        type=int,
        dest="sample_rate",
        metavar="HZ",
        default=None,
        help="IQ sample rate Hz; simulator only  "
        "(config: source.sample_rate; default: 240000)",
    )
    g.add_argument(
        "--freq",
        type=int,
        metavar="HZ",
        default=None,
        help="Centre frequency Hz; simulator default: 433920000; "
        "required for .wav files  (config: source.freq)",
    )

    return p


# ---------------------------------------------------------------------------
# connect command
# ---------------------------------------------------------------------------


def _connect(args: argparse.Namespace) -> None:
    cfg = _load_config_file(Path(args.config) if args.config else None)

    # ---- Resolve every setting: CLI flag > env var > config file > default ----
    server_url: str | None = _pick(
        args.server,
        os.environ.get("RF_AGENT_SERVER"),
        _get(cfg, "server", "url"),
    )
    token: str | None = _pick(
        args.token,
        os.environ.get("RF_AGENT_TOKEN"),
        _get(cfg, "server", "token"),
    )
    node_id: str | None = _pick(args.node_id, _get(cfg, "identity", "node_id"))

    fft_size = int(_pick(args.fft_size, _get(cfg, "source", "fft_size"), 1024))
    sample_rate = int(
        _pick(args.sample_rate, _get(cfg, "source", "sample_rate"), 240_000)
    )
    freq: int | None = _pick(args.freq, _get(cfg, "source", "freq"))
    if freq is not None:
        freq = int(freq)

    file_arg: str | None = _pick(args.file, _get(cfg, "source", "file"))
    fps: float | None = _pick(args.fps, _get(cfg, "source", "fps"))
    rate_limit_msps: float | None = _pick(
        args.rate_limit_msps, _get(cfg, "source", "rate_limit_msps")
    )

    reconnect_sec = _get(cfg, "reconnect") or {}

    # ---- Validate ----
    missing = []
    if not server_url:
        missing.append(
            "server URL   (--server, RF_AGENT_SERVER, or config server.url)"
        )  # noqa: E501
    if not token:
        missing.append(
            "bearer token  (--token, RF_AGENT_TOKEN, or config server.token)"
        )  # noqa: E501
    if not node_id:
        missing.append("node ID  (--node-id or config identity.node_id)")
    if missing:
        print("Missing required configuration:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        raise SystemExit(1)

    if fps is not None and rate_limit_msps is not None:
        raise SystemExit("--fps and --rate-limit-msps are mutually exclusive")

    # ---- Resolve file path and build IQ descriptor + source factory ----
    file_path = Path(file_arg) if file_arg else None
    if file_path is not None and file_path.suffix.lower() == ".sigmf-data":
        file_path = file_path.with_suffix(".sigmf-meta")

    iq: IQDescriptor
    source_label: str

    if file_path is not None:
        suffix = file_path.suffix.lower()

        if suffix == ".sigmf-meta":
            try:
                iq = sigmf_read_iq(file_path)
            except Exception as exc:
                raise SystemExit(f"Cannot read SigMF file: {exc}") from exc
            source_label = f"sigmf:{file_path.name}"

        elif suffix == ".wav":
            if freq is None:
                raise SystemExit(
                    "--freq is required for .wav files "
                    "(WAV has no centre-frequency metadata)"
                )
            try:
                iq = wav_read_iq(file_path, freq)
            except Exception as exc:
                raise SystemExit(f"Cannot read WAV file: {exc}") from exc
            source_label = f"wav:{file_path.name}"

        else:
            raise SystemExit(
                f"Unsupported file type {suffix!r}. "
                "Use .sigmf-meta, .sigmf-data, or .wav"
            )

    else:
        center_hz = freq if freq is not None else 433_920_000
        iq = IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=sample_rate,
            center_freq_hz=center_hz,
        )
        source_label = "simulator"

    # ---- Compute effective rate limit ----
    block_size = fft_size * iq.sample_format.bytes_per_sample
    effective_rl: float | None
    if fps is not None:
        effective_rl = float(fps) * fft_size / 1e6
    else:
        effective_rl = float(rate_limit_msps) if rate_limit_msps is not None else None

    # ---- Source factory closure ----
    if file_path is not None and file_path.suffix.lower() == ".sigmf-meta":

        def make_source(agent_cfg: AgentConfig) -> SigMFSource:
            return SigMFSource(
                meta_path=file_path,
                block_size=block_size,
                loops=None,
                rate_limit_msps=effective_rl,
            )

    elif file_path is not None:

        def make_source(agent_cfg: AgentConfig) -> WavSource:
            return WavSource(
                wav_path=file_path,
                center_freq_hz=agent_cfg.iq.center_freq_hz,
                block_size=block_size,
                loops=None,
                rate_limit_msps=effective_rl,
            )

    else:

        def make_source(agent_cfg: AgentConfig) -> SimulatorSource:
            return SimulatorSource(
                descriptor=agent_cfg.iq,
                block_size=block_size,
                rate_limit_msps=effective_rl,
            )

    # ---- Build AgentConfig ----
    agent_config = AgentConfig(
        identity=AgentIdentity(node_id=node_id),
        server=ServerConfig(url=server_url, token=token),
        rf=RFConfig(
            center_freq_hz=iq.center_freq_hz,
            sample_rate_hz=iq.sample_rate_hz,
            fft_size=fft_size,
        ),
        iq=iq,
        reconnect=ReconnectConfig(
            initial_delay_s=float(reconnect_sec.get("initial_delay_s", 2.0)),
            max_delay_s=float(reconnect_sec.get("max_delay_s", 30.0)),
            backoff_factor=float(reconnect_sec.get("backoff_factor", 2.0)),
            jitter=bool(reconnect_sec.get("jitter", True)),
        ),
    )

    # ---- Print startup summary ----
    logical_fps = iq.sample_rate_hz / fft_size
    disp_fps = (effective_rl * 1e6 / fft_size) if effective_rl else logical_fps
    print(
        f"rf-agent {_VERSION}\n"
        f"  source     = {source_label}\n"
        f"  node       = {node_id}\n"
        f"  server     = {server_url}\n"
        f"  centre     = {iq.center_freq_hz / 1e6:.3f} MHz\n"
        f"  sample_rate= {iq.sample_rate_hz / 1e6:.3f} Msps  "
        f"fft_size={fft_size}  fps={disp_fps:.1f}\n"
        "Press Ctrl-C to stop."
    )

    # ---- Run ----
    factories = make_standard_factories(make_source)
    runner = AgentRunner(config=agent_config, factories=factories)

    try:
        asyncio.run(runner.run_forever())
    except KeyboardInterrupt:
        print("\nstopped.")
    except BuildFailure as exc:
        raise SystemExit(f"Agent startup failed: {exc.__cause__}") from exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        raise SystemExit(1)

    if args.command == "connect":
        _connect(args)
