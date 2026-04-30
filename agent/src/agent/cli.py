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
import importlib
import importlib.metadata
import os
import sys
from pathlib import Path
from collections.abc import Callable
from typing import Any, BinaryIO, Protocol, cast

from agent.app.factories import make_standard_factories
from agent.utils.power import allow_sleep, prevent_sleep
from agent.app.runner import AgentRunner, BuildFailure
from agent.session import FatalSessionError
from agent.transport import AuthenticationError
from agent.config import AgentConfig, ConfigValidationError, load_config_dict
from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.source.base import IQSource
from agent.source.sigmf import SigMFSource
from agent.source.sigmf import read_iq_descriptor as sigmf_read_iq
from agent.source.simulator import SimulatorSource
from agent.source.wav import WavSource
from agent.source.wav import read_iq_descriptor as wav_read_iq

_VERSION = importlib.metadata.version("rf-agent")
_DEFAULT_CONFIG_PATHS = [
    Path("rf-agent.toml"),
    Path.home() / ".rf-agent" / "config.toml",
]


class _TomlModule(Protocol):
    def load(self, fp: BinaryIO, /) -> object: ...


def _import_toml_module() -> _TomlModule | None:
    for module_name in ("tomllib", "tomli"):
        try:
            return cast(_TomlModule, importlib.import_module(module_name))
        except ImportError:
            continue
    return None


_TOML_MODULE = _import_toml_module()


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

    if _TOML_MODULE is None:
        raise SystemExit(
            "Cannot read config file: install 'tomli' (pip install tomli) "
            "or upgrade to Python 3.11+."
        )

    with path.open("rb") as f:
        data = _TOML_MODULE.load(f)
    if not isinstance(data, dict):
        raise SystemExit(f"Config file root must be a TOML table: {path}")
    print(f"Using config: {path}")
    return cast(dict[str, Any], data)


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

    g = conn.add_argument_group("system")
    g.add_argument(
        "--prevent-sleep",
        action="store_true",
        default=False,
        help="Prevent Windows idle sleep while the agent is running (Windows only).",
    )

    return p


# ---------------------------------------------------------------------------
# connect command
# ---------------------------------------------------------------------------


def _resolve_connect(
    args: argparse.Namespace, file_cfg: dict[str, Any]
) -> tuple[AgentConfig, Callable[[AgentConfig], IQSource], str]:
    """Return (AgentConfig, source_factory, source_label).

    Precedence: CLI flags > config file > environment variables > defaults.
    Raises SystemExit on missing required settings or validation errors.
    """
    # ---- Resolve every setting: CLI flag > config file > env var > default ----
    server_url: str | None = _pick(
        args.server,
        _get(file_cfg, "server", "url"),
        os.environ.get("RF_AGENT_SERVER"),
    )
    token: str | None = _pick(
        args.token,
        _get(file_cfg, "server", "token"),
        os.environ.get("RF_AGENT_TOKEN"),
    )
    node_id: str | None = _pick(args.node_id, _get(file_cfg, "identity", "node_id"))

    fft_size = int(_pick(args.fft_size, _get(file_cfg, "source", "fft_size"), 1024))
    sample_rate = int(
        _pick(args.sample_rate, _get(file_cfg, "source", "sample_rate"), 240_000)
    )
    freq: int | None = _pick(args.freq, _get(file_cfg, "source", "freq"))
    if freq is not None:
        freq = int(freq)

    file_arg: str | None = _pick(args.file, _get(file_cfg, "source", "file"))
    fps: float | None = _pick(args.fps, _get(file_cfg, "source", "fps"))
    rate_limit_msps: float | None = _pick(
        args.rate_limit_msps, _get(file_cfg, "source", "rate_limit_msps")
    )

    # ---- Validate ----
    missing = []
    if not server_url:
        missing.append("server URL   (--server, config server.url, or RF_AGENT_SERVER)")
    if not token:
        missing.append(
            "bearer token  (--token, config server.token, or RF_AGENT_TOKEN)"
        )
    if not node_id:
        missing.append("node ID  (--node-id or config identity.node_id)")
    if missing:
        print("Missing required configuration:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        raise SystemExit(1)

    if fps is not None and rate_limit_msps is not None:
        raise SystemExit("--fps and --rate-limit-msps are mutually exclusive")

    # ---- Resolve file path and build IQ descriptor ----
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

        def _make_sigmf_source(agent_cfg: AgentConfig) -> IQSource:
            return SigMFSource(
                meta_path=file_path,
                block_size=block_size,
                loops=None,
                rate_limit_msps=effective_rl,
            )

        make_source = _make_sigmf_source

    elif file_path is not None:

        def _make_wav_source(agent_cfg: AgentConfig) -> IQSource:
            return WavSource(
                wav_path=file_path,
                center_freq_hz=agent_cfg.iq.center_freq_hz,
                block_size=block_size,
                loops=None,
                rate_limit_msps=effective_rl,
            )

        make_source = _make_wav_source

    else:

        def _make_simulator_source(agent_cfg: AgentConfig) -> IQSource:
            return SimulatorSource(
                descriptor=agent_cfg.iq,
                block_size=block_size,
                rate_limit_msps=effective_rl,
            )

        make_source = _make_simulator_source

    # ---- Build typed config through the validation boundary ----
    raw: dict[str, Any] = {
        "server": {"url": server_url, "token": token},
        "identity": {"node_id": node_id},
        "iq": {
            "sample_format": iq.sample_format.value,
            "endianness": iq.endianness.value,
            "layout": iq.layout.value,
            "sample_rate_hz": iq.sample_rate_hz,
            "center_freq_hz": iq.center_freq_hz,
        },
        "rf": {
            "center_freq_hz": iq.center_freq_hz,
            "sample_rate_hz": iq.sample_rate_hz,
            "fft_size": fft_size,
        },
    }
    for section in ("reconnect", "queues", "telemetry", "bandwidth"):
        val = file_cfg.get(section)
        if val is not None:
            raw[section] = val

    try:
        agent_config = load_config_dict(raw)
    except ConfigValidationError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    return agent_config, make_source, source_label


def _connect(args: argparse.Namespace) -> None:
    file_cfg = _load_config_file(Path(args.config) if args.config else None)
    agent_config, make_source, source_label = _resolve_connect(args, file_cfg)

    iq = agent_config.iq
    fft_size = agent_config.rf.fft_size
    fps_arg: float | None = getattr(args, "fps", None)
    rl_arg: float | None = getattr(args, "rate_limit_msps", None)
    if fps_arg is not None:
        effective_rl: float | None = float(fps_arg) * fft_size / 1e6
    elif rl_arg is not None:
        effective_rl = float(rl_arg)
    else:
        effective_rl = None

    logical_fps = iq.sample_rate_hz / fft_size
    disp_fps = (effective_rl * 1e6 / fft_size) if effective_rl else logical_fps
    print(
        f"rf-agent {_VERSION}\n"
        f"  source     = {source_label}\n"
        f"  node       = {agent_config.identity.node_id}\n"
        f"  server     = {agent_config.server.url}\n"
        f"  centre     = {iq.center_freq_hz / 1e6:.3f} MHz\n"
        f"  sample_rate= {iq.sample_rate_hz / 1e6:.3f} Msps  "
        f"fft_size={fft_size}  fps={disp_fps:.1f}\n"
        "Press Ctrl-C to stop."
    )

    factories = make_standard_factories(make_source)
    runner = AgentRunner(config=agent_config, factories=factories)

    if args.prevent_sleep:
        prevent_sleep()
    try:
        asyncio.run(runner.run_forever())
    except KeyboardInterrupt:
        print("\nstopped.")
    except AuthenticationError:
        raise SystemExit(
            "Authentication failed: check your token (--token / RF_AGENT_TOKEN)"
        )
    except FatalSessionError as exc:
        raise SystemExit(f"Server rejected connection (not retrying): {exc}") from exc
    except BuildFailure as exc:
        raise SystemExit(f"Agent startup failed: {exc.__cause__}") from exc
    finally:
        if args.prevent_sleep:
            allow_sleep()


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
