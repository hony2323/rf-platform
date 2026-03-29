"""Unit tests for parse_iq — Batch 1 + real SigMF data.

Covers: float32 known-signal anchor, roundtrip values, int16/uint8
normalization, DC offset removal, error cases, and parser correctness
against the LTE uplink SigMF fixture (ci16_le, 847 MHz, 30.72 Msps).
"""

from __future__ import annotations

import math
import struct

import numpy as np

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.processing.parse_iq import IQParseErrorCode, IQParseResult, parse_iq
from agent.tests.conftest import SigMFBuffer  # noqa: F401 — used as fixture type hint

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


def interleave_float32(i_ch: np.ndarray, q_ch: np.ndarray) -> bytes:
    buf = np.empty(len(i_ch) * 2, dtype=np.float32)
    buf[0::2] = i_ch
    buf[1::2] = q_ch
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
    i_ch = np.cos(2 * math.pi * f_tone * t).astype(np.float32) * 0.5
    q_ch = np.sin(2 * math.pi * f_tone * t).astype(np.float32) * 0.5
    buffer = interleave_float32(i_ch, q_ch)

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
    descriptor = make_descriptor(
        sample_format=SampleFormat.INT16, dc_offset_remove=False
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32
    assert result.sample_count == len(raw) // 2
    expected = np.array([v / 32768.0 for v in raw], dtype=np.float32)
    np.testing.assert_array_almost_equal(result.samples, expected)


def test_parse_uint8_normalizes_using_center_and_scale() -> None:
    raw = [0, 127, 255, 128]
    buffer = bytes(raw)
    descriptor = make_descriptor(
        sample_format=SampleFormat.UINT8, dc_offset_remove=False
    )

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
    i_ch = np.full(n, 0.3, dtype=np.float32)
    q_ch = np.full(n, -0.2, dtype=np.float32)
    buffer = interleave_float32(i_ch, q_ch)
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


# ---------------------------------------------------------------------------
# Real SigMF data — LTE uplink fixture (ci16_le, 847 MHz, 30.72 Msps)
# 256 000 bytes → 64 000 complex samples → 128 000 floats
# ---------------------------------------------------------------------------

_FIXTURE_BYTE_COUNT = 256_000
_FIXTURE_SAMPLE_COUNT = 64_000   # file bytes / bytes_per_sample (4 for ci16_le)


async def test_parse_real_ci16_succeeds(lte_ci16_raw: SigMFBuffer) -> None:
    result = parse_iq(lte_ci16_raw.descriptor, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)


async def test_parse_real_ci16_sample_count_matches_file_size(
    lte_ci16_raw: SigMFBuffer,
) -> None:
    result = parse_iq(lte_ci16_raw.descriptor, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)
    assert result.sample_count == _FIXTURE_SAMPLE_COUNT


async def test_parse_real_ci16_output_length_is_sample_count_times_two(
    lte_ci16_raw: SigMFBuffer,
) -> None:
    result = parse_iq(lte_ci16_raw.descriptor, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)
    assert len(result.samples) == result.sample_count * 2


async def test_parse_real_ci16_output_dtype_is_float32(
    lte_ci16_raw: SigMFBuffer,
) -> None:
    result = parse_iq(lte_ci16_raw.descriptor, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32


async def test_parse_real_ci16_normalized_values_within_unit_range(
    lte_ci16_raw: SigMFBuffer,
) -> None:
    result = parse_iq(lte_ci16_raw.descriptor, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)


async def test_parse_real_ci16_signal_has_nonzero_energy(
    lte_ci16_raw: SigMFBuffer,
) -> None:
    """Guards against silent zero-fill or byte-order bugs that produce a flat signal."""
    result = parse_iq(lte_ci16_raw.descriptor, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)
    assert float(np.std(result.samples)) > 0.01


async def test_parse_real_ci16_dc_removal_reduces_channel_means(
    lte_ci16_raw: SigMFBuffer,
) -> None:
    descriptor_no_dc = lte_ci16_raw.descriptor
    descriptor_dc = IQDescriptor(
        sample_format=descriptor_no_dc.sample_format,
        endianness=descriptor_no_dc.endianness,
        layout=descriptor_no_dc.layout,
        sample_rate_hz=descriptor_no_dc.sample_rate_hz,
        center_freq_hz=descriptor_no_dc.center_freq_hz,
        normalize=descriptor_no_dc.normalize,
        dc_offset_remove=True,
    )
    result = parse_iq(descriptor_dc, lte_ci16_raw.raw_bytes)
    assert isinstance(result, IQParseResult)
    assert abs(float(result.samples[0::2].mean())) < 1e-4  # I channel
    assert abs(float(result.samples[1::2].mean())) < 1e-4  # Q channel
