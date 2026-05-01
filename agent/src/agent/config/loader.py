"""Config loading boundary.

Converts raw external input (dict/file/env/CLI-facing shape) into a fully
typed AgentConfig.  After this boundary, the rest of the codebase works only
with typed objects.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from typing import Any, cast

from agent.config.errors import ConfigValidationError
from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    WireEncoding,
    WindowFunction,
)

from . import (
    AgentConfig,
    AgentIdentity,
    BandwidthConfig,
    QueueConfig,
    ReconnectConfig,
    ServerConfig,
    TelemetryConfig,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config_dict(raw: Mapping[str, Any]) -> AgentConfig:
    """Parse and validate *raw* config input and return a typed AgentConfig.

    Raises ConfigValidationError with a field-specific message on any
    validation failure.
    """
    server = _load_server(raw.get("server"))
    identity = _load_identity(raw.get("identity"))
    iq = _load_iq(raw.get("iq"))
    rf = _load_rf(raw.get("rf"))
    stream_id = _load_stream_id(raw.get("stream_id", "default"))
    wire_encoding = _load_wire_encoding(raw.get("wire_encoding", "binary_ws"))
    queues = _load_queues(raw.get("queues"))
    telemetry = _load_telemetry(raw.get("telemetry"))
    reconnect = _load_reconnect(raw.get("reconnect"))
    bandwidth = _load_bandwidth(raw.get("bandwidth"))

    _check_iq_rf_consistency(iq, rf)

    return AgentConfig(
        identity=identity,
        server=server,
        rf=rf,
        iq=iq,
        stream_id=stream_id,
        wire_encoding=wire_encoding,
        queues=queues,
        telemetry=telemetry,
        reconnect=reconnect,
        bandwidth=bandwidth,
    )


# ---------------------------------------------------------------------------
# Private section loaders
# ---------------------------------------------------------------------------


def _require_section(value: Any, section: str) -> Mapping[str, Any]:
    if value is None:
        raise ConfigValidationError(f"{section}: required")
    if not isinstance(value, Mapping):
        raise ConfigValidationError(f"{section}: must be a mapping")
    return value


def _load_server(raw: Any) -> ServerConfig:
    sec = _require_section(raw, "server")

    url = sec.get("url")
    if url is None:
        raise ConfigValidationError("server.url: required")
    if not isinstance(url, str):
        raise ConfigValidationError("server.url: must be a string")
    if not url:
        raise ConfigValidationError("server.url: must be a non-empty string")
    if not (url.startswith("ws://") or url.startswith("wss://")):
        raise ConfigValidationError("server.url: must start with 'ws://' or 'wss://'")

    token = sec.get("token")
    if token is None:
        raise ConfigValidationError("server.token: required")
    if not isinstance(token, str) or not token:
        raise ConfigValidationError("server.token: must be a non-empty string")

    return ServerConfig(url=url, token=token)


def _load_identity(raw: Any) -> AgentIdentity:
    sec = _require_section(raw, "identity")

    node_id = sec.get("node_id")
    if node_id is None:
        raise ConfigValidationError("identity.node_id: required")
    if not isinstance(node_id, str) or not node_id:
        raise ConfigValidationError("identity.node_id: must be a non-empty string")

    agent_version_raw = sec.get("agent_version", "0.3.0")
    if not isinstance(agent_version_raw, str) or not agent_version_raw:
        raise ConfigValidationError(
            "identity.agent_version: must be a non-empty string"
        )

    return AgentIdentity(node_id=node_id, agent_version=agent_version_raw)


def _load_iq(raw: Any) -> IQDescriptor:
    sec = _require_section(raw, "iq")

    sample_format = _load_enum(
        sec.get("sample_format"),
        SampleFormat,
        "iq.sample_format",
        required=True,
    )
    endianness = _load_enum(
        sec.get("endianness"),
        Endianness,
        "iq.endianness",
        required=True,
    )
    layout = _load_enum(
        sec.get("layout"),
        Layout,
        "iq.layout",
        required=True,
    )
    if layout is not None and layout != Layout.INTERLEAVED:
        raise ConfigValidationError("iq.layout: only 'interleaved' is supported in MVP")

    sample_rate_hz = _require_positive_int(
        sec.get("sample_rate_hz"), "iq.sample_rate_hz"
    )
    center_freq_hz = _require_positive_int(
        sec.get("center_freq_hz"), "iq.center_freq_hz"
    )

    dc_offset_remove = _load_bool(
        sec.get("dc_offset_remove", True), "iq.dc_offset_remove"
    )
    normalize = _load_bool(sec.get("normalize", True), "iq.normalize")

    return IQDescriptor(
        sample_format=cast(SampleFormat, sample_format),
        endianness=cast(Endianness, endianness),
        layout=cast(Layout, layout),
        sample_rate_hz=sample_rate_hz,
        center_freq_hz=center_freq_hz,
        dc_offset_remove=dc_offset_remove,
        normalize=normalize,
    )


def _load_rf(raw: Any) -> RFConfig:
    sec = _require_section(raw, "rf")

    center_freq_hz = _require_positive_int(
        sec.get("center_freq_hz"), "rf.center_freq_hz"
    )
    sample_rate_hz = _require_positive_int(
        sec.get("sample_rate_hz"), "rf.sample_rate_hz"
    )
    fft_size = _require_positive_int(sec.get("fft_size"), "rf.fft_size")

    window_fn = _load_enum(
        sec.get("window_fn", "hann"),
        WindowFunction,
        "rf.window_fn",
        required=False,
    )

    bin_count_raw = sec.get("bin_count")
    bin_count: int | None = None
    if bin_count_raw is not None:
        bin_count = _require_positive_int(bin_count_raw, "rf.bin_count")
        if bin_count > fft_size:
            raise ConfigValidationError(
                f"rf.bin_count: must not exceed rf.fft_size ({fft_size}),"
                f" got {bin_count}"
            )

    return RFConfig(
        center_freq_hz=center_freq_hz,
        sample_rate_hz=sample_rate_hz,
        fft_size=fft_size,
        window_fn=(
            cast(WindowFunction, window_fn)
            if window_fn is not None
            else WindowFunction.HANN
        ),
        bin_count=bin_count,
    )


def _load_stream_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigValidationError("stream_id: must be a non-empty string")
    return value


def _load_wire_encoding(value: Any) -> WireEncoding:
    return cast(
        WireEncoding, _load_enum(value, WireEncoding, "wire_encoding", required=True)
    )


def _load_queues(raw: Any) -> QueueConfig:
    if raw is None:
        return QueueConfig()
    sec = _require_section(raw, "queues")
    iq_queue_size = _require_positive_int(
        sec.get("iq_queue_size", 4), "queues.iq_queue_size"
    )
    frame_queue_size = _require_positive_int(
        sec.get("frame_queue_size", 8), "queues.frame_queue_size"
    )
    return QueueConfig(iq_queue_size=iq_queue_size, frame_queue_size=frame_queue_size)


def _load_telemetry(raw: Any) -> TelemetryConfig:
    if raw is None:
        return TelemetryConfig()
    sec = _require_section(raw, "telemetry")
    heartbeat = _require_positive_number(
        sec.get("heartbeat_interval_s", 5.0), "telemetry.heartbeat_interval_s"
    )
    status = _require_positive_number(
        sec.get("status_interval_s", 10.0), "telemetry.status_interval_s"
    )
    return TelemetryConfig(heartbeat_interval_s=heartbeat, status_interval_s=status)


def _load_reconnect(raw: Any) -> ReconnectConfig:
    if raw is None:
        return ReconnectConfig()
    sec = _require_section(raw, "reconnect")
    initial = _require_positive_number(
        sec.get("initial_delay_s", 1.0), "reconnect.initial_delay_s"
    )
    max_delay = _require_positive_number(
        sec.get("max_delay_s", 30.0), "reconnect.max_delay_s"
    )
    backoff = sec.get("backoff_factor", 2.0)
    if not isinstance(backoff, (int, float)) or isinstance(backoff, bool):
        raise ConfigValidationError("reconnect.backoff_factor: must be a number")
    if backoff < 1.0:
        raise ConfigValidationError("reconnect.backoff_factor: must be >= 1.0")
    if max_delay < initial:
        raise ConfigValidationError(
            "reconnect.max_delay_s: must be >= reconnect.initial_delay_s"
        )
    jitter = _load_bool(sec.get("jitter", True), "reconnect.jitter")
    return ReconnectConfig(
        initial_delay_s=initial,
        max_delay_s=max_delay,
        backoff_factor=float(backoff),
        jitter=jitter,
    )


def _load_bandwidth(raw: Any) -> BandwidthConfig:
    if raw is None:
        return BandwidthConfig()
    sec = _require_section(raw, "bandwidth")

    max_bps_raw = sec.get("max_bytes_per_sec")
    max_bytes_per_sec: int | None = None
    if max_bps_raw is not None:
        if not isinstance(max_bps_raw, int) or isinstance(max_bps_raw, bool):
            raise ConfigValidationError(
                "bandwidth.max_bytes_per_sec: must be an integer"
            )
        if max_bps_raw <= 0:
            raise ConfigValidationError(
                f"bandwidth.max_bytes_per_sec: must be a positive integer,"
                f" got {max_bps_raw}"
            )
        max_bytes_per_sec = max_bps_raw

    strategy_raw = sec.get("strategy", "decimate")
    if strategy_raw not in ("decimate", "drop"):
        raise ConfigValidationError(
            f"bandwidth.strategy: unsupported value {strategy_raw!r};"
            f" allowed: 'decimate', 'drop'"
        )

    return BandwidthConfig(
        max_bytes_per_sec=max_bytes_per_sec,
        strategy=strategy_raw,
    )


# ---------------------------------------------------------------------------
# Cross-field consistency
# ---------------------------------------------------------------------------


def _check_iq_rf_consistency(iq: IQDescriptor, rf: RFConfig) -> None:
    if iq.sample_rate_hz != rf.sample_rate_hz:
        raise ConfigValidationError(
            f"iq.sample_rate_hz: must equal rf.sample_rate_hz"
            f" ({rf.sample_rate_hz}), got {iq.sample_rate_hz}"
        )
    if iq.center_freq_hz != rf.center_freq_hz:
        raise ConfigValidationError(
            f"iq.center_freq_hz: must equal rf.center_freq_hz"
            f" ({rf.center_freq_hz}), got {iq.center_freq_hz}"
        )


# ---------------------------------------------------------------------------
# Primitive validators
# ---------------------------------------------------------------------------


def _require_positive_int(value: Any, field: str) -> int:
    if value is None:
        raise ConfigValidationError(f"{field}: required")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigValidationError(f"{field}: must be an integer")
    if value <= 0:
        raise ConfigValidationError(f"{field}: must be a positive integer, got {value}")
    return value


def _require_positive_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigValidationError(f"{field}: must be a number")
    if value <= 0:
        raise ConfigValidationError(f"{field}: must be positive, got {value}")
    return float(value)


def _load_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigValidationError(f"{field}: must be a boolean")
    return value


_ENUM_VALUES: dict[type[enum.Enum], dict[str, enum.Enum]] = {}


def _load_enum(
    value: Any,
    enum_cls: type[enum.Enum],
    field: str,
    *,
    required: bool,
) -> enum.Enum | None:
    if value is None:
        if required:
            raise ConfigValidationError(f"{field}: required")
        return None

    # Build a lookup by .value the first time we see this enum class
    if enum_cls not in _ENUM_VALUES:
        _ENUM_VALUES[enum_cls] = {member.value: member for member in enum_cls}
    lookup = _ENUM_VALUES[enum_cls]

    if value not in lookup:
        allowed = ", ".join(f"'{v}'" for v in lookup)
        raise ConfigValidationError(
            f"{field}: unsupported value {value!r}; allowed: {allowed}"
        )
    return lookup[value]
