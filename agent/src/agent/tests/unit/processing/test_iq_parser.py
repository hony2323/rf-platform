"""Unit tests for parse_iq — Batch 1.

Covers: float32 known-signal anchor, roundtrip values, int16/uint8
normalization, DC offset removal, and error cases.
"""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.processing.parse_iq import IQParseErrorCode, IQParseResult, parse_iq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_descriptor(**kwargs: object) -> IQDescriptor:
    defaults: dict[str, object] = {
        "sample_format": SampleFormat.FLOAT32,
        "endianness": Endianness.LITTLE,
        "layout": Layout.INTERLEAVED,
        "sample_rate_hz": 2_400_000,
        "center_freq_hz": 433_920_000,
        "normalize": True,
        "dc_offset_remove": True,
    }
    defaults.update(kwargs)
    return IQDescriptor(**defaults)  # type: ignore[arg-type]


def interleave_float32(I: np.ndarray, Q: np.ndarray) -> bytes:
    buf = np.empty(len(I) * 2, dtype=np.float32)
    buf[0::2] = I
    buf[1::2] = Q
    return buf.tobytes()


# ---------------------------------------------------------------------------
# Batch 1
# ---------------------------------------------------------------------------


def test_parse_float32_interleaved_known_signal_peak_bin_matches_expected() -> None:
    """Anchor test: validates byte order, interleaving, normalization, and
    that the output is usable by FFT with the expected fftshifted peak bin."""
    f_tone = 100_000          # Hz
    sample_rate = 2_400_000   # Hz
    fft_size = n_samples = 131_072
    bin_size_hz = sample_rate / fft_size

    t = np.arange(n_samples) / sample_rate
    I = np.cos(2 * math.pi * f_tone * t).astype(np.float32) * 0.5
    Q = np.sin(2 * math.pi * f_tone * t).astype(np.float32) * 0.5
    buffer = interleave_float32(I, Q)

    descriptor = make_descriptor(dc_offset_remove=False)
    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)

    complex_samples = result.samples[0::2] + 1j * result.samples[1::2]
    fft_out = np.fft.fftshift(np.fft.fft(complex_samples))

    expected_bin = round(f_tone / bin_size_hz) + fft_size // 2
    peak_bin = int(np.argmax(np.abs(fft_out)))

    assert peak_bin == expected_bin


def test_parse_float32_roundtrip_values_preserved() -> None:
    """Float32 values pass through unchanged; catches byte-order bugs without
    needing FFT machinery."""
    values = [0.1, -0.2, 0.3, -0.4, 0.5, -0.5]
    buffer = struct.pack(f"<{len(values)}f", *values)
    descriptor = make_descriptor(normalize=False, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32
    assert result.sample_count == len(values) // 2
    np.testing.assert_array_almost_equal(
        result.samples, np.array(values, dtype=np.float32)
    )


def test_parse_int16_normalizes_using_divide_by_32768() -> None:
    raw = [32767, -32768, 0, 16384]
    buffer = struct.pack(f"<{len(raw)}h", *raw)
    descriptor = make_descriptor(sample_format=SampleFormat.INT16, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32
    assert result.sample_count == len(raw) // 2
    expected = np.array([v / 32768.0 for v in raw], dtype=np.float32)
    np.testing.assert_array_almost_equal(result.samples, expected)


def test_parse_uint8_normalizes_using_center_and_scale() -> None:
    raw = [0, 127, 255, 128]
    buffer = bytes(raw)
    descriptor = make_descriptor(sample_format=SampleFormat.UINT8, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32
    expected = np.array([(x - 127.5) / 127.5 for x in raw], dtype=np.float32)
    np.testing.assert_array_almost_equal(result.samples, expected)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)


def test_parse_applies_dc_offset_removal_when_enabled() -> None:
    n = 64
    # bias: I channel at +0.3, Q channel at -0.2
    I = np.full(n, 0.3, dtype=np.float32)
    Q = np.full(n, -0.2, dtype=np.float32)
    buffer = interleave_float32(I, Q)
    descriptor = make_descriptor(normalize=False, dc_offset_remove=True)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert abs(float(result.samples[0::2].mean())) < 1e-5
    assert abs(float(result.samples[1::2].mean())) < 1e-5


def test_parse_rejects_incomplete_sample() -> None:
    """bytes_per_sample for float32 is 8 (I+Q pair); 4 bytes = half a sample."""
    buffer = struct.pack("<f", 0.5)  # 4 bytes only
    descriptor = make_descriptor()

    result = parse_iq(descriptor, buffer)

    assert not isinstance(result, IQParseResult)
    assert result.code == IQParseErrorCode.INCOMPLETE_SAMPLE
