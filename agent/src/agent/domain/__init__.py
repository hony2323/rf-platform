"""Core domain models.

These are plain data containers. No I/O, no behavior beyond validation.
Every other module depends on these; these depend on nothing.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SampleFormat(enum.Enum):
    FLOAT32 = "float32"
    INT16 = "int16"
    FLOAT64 = "float64"
    UINT8 = "uint8"

    @property
    def bytes_per_sample(self) -> int:
        """Bytes per complex sample (I + Q)."""
        return {
            SampleFormat.FLOAT32: 8,
            SampleFormat.INT16: 4,
            SampleFormat.FLOAT64: 16,
            SampleFormat.UINT8: 2,
        }[self]


class Endianness(enum.Enum):
    LITTLE = "little"
    BIG = "big"


class Layout(enum.Enum):
    INTERLEAVED = "interleaved"


class BinOrder(enum.Enum):
    LOW_TO_HIGH = "low_to_high"
    NATURAL = "natural"


class WindowFunction(enum.Enum):
    HANN = "hann"


class ConnectionState(enum.Enum):
    """Agent session state machine states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CONFIGURED = "configured"
    STREAMING = "streaming"


class WireEncoding(enum.Enum):
    JSON_BASE64 = "json_base64"
    BINARY_WS = "binary_ws"


# ---------------------------------------------------------------------------
# IQ input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IQDescriptor:
    """Describes the format of an IQ sample buffer.

    Matches iq_input_schema.md exactly.
    """

    sample_format: SampleFormat
    endianness: Endianness
    layout: Layout
    sample_rate_hz: int
    center_freq_hz: int
    dc_offset_remove: bool = True
    normalize: bool = True

    @property
    def bytes_per_sample(self) -> int:
        return self.sample_format.bytes_per_sample


# ---------------------------------------------------------------------------
# RF / FFT config (mirrors stream_config.rf + fft_semantics)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RFConfig:
    center_freq_hz: int
    sample_rate_hz: int
    fft_size: int
    window_fn: WindowFunction = WindowFunction.HANN
    # bin_count: number of bins in the wire payload. None means equal to fft_size
    # (MVP default). Distinct from fft_size per the protocol spec — the codec must
    # serialize this field explicitly.
    bin_count: int | None = None

    @property
    def effective_bin_count(self) -> int:
        """Payload bin count. Equals fft_size for MVP (no frequency cropping)."""
        return self.bin_count if self.bin_count is not None else self.fft_size

    @property
    def bin_size_hz(self) -> float:
        return self.sample_rate_hz / self.fft_size

    @property
    def baseband_start_hz(self) -> float:
        return -(self.sample_rate_hz / 2)

    @property
    def baseband_end_hz(self) -> float:
        return self.sample_rate_hz / 2


@dataclass(frozen=True)
class FFTSemantics:
    """MVP defaults from protocol v0.3."""

    kind: str = "power"
    scale: str = "log"
    unit: str = "dBFS"
    numeric_type: str = "float32"
    bin_order: BinOrder = BinOrder.LOW_TO_HIGH


# ---------------------------------------------------------------------------
# Spectrum frame (output of processing, input to session)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpectrumFrame:
    """A single processed FFT frame ready for transmission.

    `payload` is float32 log-power values, bin_order=low_to_high.
    Length = bin_count (which may differ from fft_size in post-MVP).
    """

    payload: bytes  # float32 LE, length = bin_count * 4
    timestamp_utc: str  # ISO 8601, capture time
    bin_count: int


# ---------------------------------------------------------------------------
# Hardware info (informational, sent in connect)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HardwareInfo:
    vendor: str | None = None
    model: str | None = None
    serial: str | None = None


# ---------------------------------------------------------------------------
# Agent status telemetry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DropCounters:
    local_throttle: int = 0
    queue_overflow: int = 0
    server_rejected: int = 0
    parse_errors: int = 0


@dataclass(frozen=True)
class PipelineLatencies:
    parse_iq_p50_ms: float
    parse_iq_p99_ms: float
    fft_p50_ms: float
    fft_p99_ms: float
    encode_send_p50_ms: float
    encode_send_p99_ms: float
    iq_queue_depth_avg: float
    frame_queue_depth_avg: float


@dataclass(frozen=True)
class AgentMetrics:
    cpu_usage_pct: float
    throttled: bool
    tx_bytes_per_sec: int
    queue_depth: int
    queue_fill_pct: float
    drops: DropCounters = field(default_factory=DropCounters)
    pipeline: PipelineLatencies | None = None
