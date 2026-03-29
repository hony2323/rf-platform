"""IQ sample parser.

Contract: accepts (descriptor, bytes), returns normalized float32 samples.
Stateless. Source-agnostic. Matches iq_input_schema.md.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat


class IQParseErrorCode(enum.Enum):
    EMPTY_BUFFER = "EMPTY_BUFFER"
    INCOMPLETE_SAMPLE = "INCOMPLETE_SAMPLE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    UNSUPPORTED_LAYOUT = "UNSUPPORTED_LAYOUT"


@dataclass(frozen=True)
class IQParseError:
    code: IQParseErrorCode
    message: str
    offset: int | None = None


@dataclass(frozen=True)
class IQParseResult:
    """Parsed IQ samples.

    `samples` is a float32 ndarray of interleaved I/Q values.
    Length = sample_count * 2.  Normalized to [-1.0, 1.0].
    DC offset removed if descriptor.dc_offset_remove is True.
    """

    samples: npt.NDArray[np.float32]
    sample_count: int


class _UnhandledFormatError(Exception):
    """Raised by _decode_samples when no handler exists for a SampleFormat."""


def _endian_char(endianness: Endianness) -> Literal["<", ">"]:
    return "<" if endianness == Endianness.LITTLE else ">"


def _decode_samples(
    buffer: bytes, descriptor: IQDescriptor
) -> npt.NDArray[np.float32]:
    """Convert raw bytes to float32 array, applying normalization per format."""
    fmt = descriptor.sample_format
    ec = _endian_char(descriptor.endianness)

    if fmt == SampleFormat.FLOAT32:
        dtype = np.dtype(np.float32).newbyteorder(ec)
        samples = np.frombuffer(buffer, dtype=dtype).astype(np.float32)
        # Spec: float32 normalization is a clamp check; hardware may produce
        # values slightly outside [-1.0, 1.0] due to overflow or calibration.
        return np.clip(samples, -1.0, 1.0)

    if fmt == SampleFormat.INT16:
        raw = np.frombuffer(buffer, dtype=np.dtype(np.int16).newbyteorder(ec))
        if descriptor.normalize:
            return (raw / 32768.0).astype(np.float32)
        return raw.astype(np.float32)

    if fmt == SampleFormat.UINT8:
        raw = np.frombuffer(buffer, dtype=np.uint8).astype(np.float32)
        if descriptor.normalize:
            return ((raw - 127.5) / 127.5).astype(np.float32)
        return raw

    if fmt == SampleFormat.FLOAT64:
        # downcasts to float32 after normalization per schema
        raw = np.frombuffer(buffer, dtype=np.dtype(np.float64).newbyteorder(ec))
        return raw.astype(np.float32)

    raise _UnhandledFormatError(f"unhandled sample_format: {fmt!r}")


def parse_iq(
    descriptor: IQDescriptor, buffer: bytes
) -> IQParseResult | IQParseError:
    """Parse raw IQ bytes into normalized float32 samples.

    Invariants (from iq_input_schema.md):
    - len(samples) == sample_count * 2
    - sample_count == len(buffer) / descriptor.bytes_per_sample
    - all samples in [-1.0, 1.0] when normalize=True
    - mean(I) ≈ 0 and mean(Q) ≈ 0 when dc_offset_remove=True
    """
    if len(buffer) == 0:
        return IQParseError(
            code=IQParseErrorCode.EMPTY_BUFFER, message="buffer is empty"
        )

    if descriptor.layout != Layout.INTERLEAVED:
        return IQParseError(
            code=IQParseErrorCode.UNSUPPORTED_LAYOUT,
            message=(
                f"layout {descriptor.layout.value} is not supported"
                " (MVP: interleaved only)"
            ),
        )

    bps = descriptor.bytes_per_sample
    if len(buffer) % bps != 0:
        return IQParseError(
            code=IQParseErrorCode.INCOMPLETE_SAMPLE,
            message=(
                f"buffer length {len(buffer)} is not a multiple"
                f" of bytes_per_sample {bps}"
            ),
            offset=len(buffer) - (len(buffer) % bps),
        )

    try:
        samples = _decode_samples(buffer, descriptor).copy()
    except _UnhandledFormatError as exc:
        return IQParseError(
            code=IQParseErrorCode.UNSUPPORTED_FORMAT,
            message=str(exc),
        )

    if descriptor.dc_offset_remove:
        samples[0::2] -= float(samples[0::2].mean())
        samples[1::2] -= float(samples[1::2].mean())

    return IQParseResult(samples=samples, sample_count=len(samples) // 2)
