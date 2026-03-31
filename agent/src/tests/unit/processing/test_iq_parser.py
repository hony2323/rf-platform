"""Unit tests for parse_iq — Batch 1 + real SigMF data.

Covers: float32 known-signal anchor, roundtrip values, int16/uint8
normalization, DC offset removal, error cases, and parser correctness
against the LTE uplink SigMF fixture (ci16_le, 847 MHz, 30.72 Msps).
"""

from __future__ import annotations

import math
import struct

import numpy as np
import numpy.typing as npt
import pytest

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.processing.parse_iq import IQParseErrorCode, IQParseResult, parse_iq
from tests.conftest import SigMFBuffer  # noqa: F401 — used as fixture type hint

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
    f_tone = 100_000  # Hz
    sample_rate = 2_400_000  # Hz
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
_FIXTURE_SAMPLE_COUNT = 64_000  # file bytes / bytes_per_sample (4 for ci16_le)


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


# ---------------------------------------------------------------------------
# Issue fixes and test-gap coverage
# ---------------------------------------------------------------------------


def test_parse_empty_buffer_returns_empty_buffer_error() -> None:
    """Direct test for the EMPTY_BUFFER error code (gap #9)."""
    descriptor = make_descriptor()
    result = parse_iq(descriptor, b"")
    assert not isinstance(result, IQParseResult)
    assert result.code == IQParseErrorCode.EMPTY_BUFFER


def test_parse_float32_normalize_true_clips_out_of_range_values() -> None:
    """float32 with normalize=True: values outside [-1.0, 1.0] must be clipped."""
    values = [2.0, -3.0, 0.5, 0.5]  # 2 complex samples
    buffer = struct.pack(f"<{len(values)}f", *values)
    descriptor = make_descriptor(normalize=True, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)
    assert result.samples[0] == pytest.approx(1.0)   # 2.0 clipped
    assert result.samples[1] == pytest.approx(-1.0)  # -3.0 clipped


def test_parse_float32_normalize_false_preserves_out_of_range_values() -> None:
    """float32 with normalize=False: values outside [-1.0, 1.0] must pass through."""
    values = [2.0, -3.0, 0.5, 0.5]
    buffer = struct.pack(f"<{len(values)}f", *values)
    descriptor = make_descriptor(normalize=False, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    np.testing.assert_array_almost_equal(
        result.samples, np.array(values, dtype=np.float32)
    )


def test_parse_float32_values_within_range_are_unchanged() -> None:
    """normalize=True must not alter values already within [-1.0, 1.0]."""
    values = [0.3, -0.7, 1.0, -1.0]
    buffer = struct.pack(f"<{len(values)}f", *values)
    descriptor = make_descriptor(normalize=True, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    np.testing.assert_array_almost_equal(
        result.samples, np.array(values, dtype=np.float32)
    )


def test_parse_int16_normalize_false_returns_raw_integer_scale() -> None:
    """normalize=False for int16: values NOT divided by 32768 (gap #10)."""
    raw = [1000, -2000, 32767, -32768]
    buffer = struct.pack(f"<{len(raw)}h", *raw)
    descriptor = make_descriptor(
        sample_format=SampleFormat.INT16,
        normalize=False,
        dc_offset_remove=False,
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    expected = np.array(raw, dtype=np.float32)
    np.testing.assert_array_equal(result.samples, expected)


def test_parse_uint8_normalize_false_returns_raw_byte_values() -> None:
    """normalize=False for uint8: values NOT shifted/scaled (gap #10)."""
    raw = [0, 64, 128, 255]
    buffer = bytes(raw)
    descriptor = make_descriptor(
        sample_format=SampleFormat.UINT8,
        normalize=False,
        dc_offset_remove=False,
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    expected = np.array(raw, dtype=np.float32)
    np.testing.assert_array_equal(result.samples, expected)


def test_parse_big_endian_int16_normalizes_correctly() -> None:
    """Big-endian int16 path (gap #7): byte-swapped values must decode correctly."""
    raw = [32767, -32768]  # 1 complex sample
    buffer = struct.pack(f">{len(raw)}h", *raw)  # big-endian
    descriptor = make_descriptor(
        sample_format=SampleFormat.INT16,
        endianness=Endianness.BIG,
        dc_offset_remove=False,
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    expected = np.array([v / 32768.0 for v in raw], dtype=np.float32)
    np.testing.assert_array_almost_equal(result.samples, expected)


def test_parse_big_endian_int16_differs_from_little_endian() -> None:
    """Same bytes interpreted as BE vs LE produce different values (sanity check)."""
    # 0x01 0x00 = 256 in big-endian, 1 in little-endian
    buffer = struct.pack(">2h", 256, -256)  # big-endian bytes
    desc_le = make_descriptor(
        sample_format=SampleFormat.INT16,
        endianness=Endianness.LITTLE,
        normalize=False,
        dc_offset_remove=False,
    )
    desc_be = make_descriptor(
        sample_format=SampleFormat.INT16,
        endianness=Endianness.BIG,
        normalize=False,
        dc_offset_remove=False,
    )

    result_le = parse_iq(desc_le, buffer)
    result_be = parse_iq(desc_be, buffer)

    assert isinstance(result_le, IQParseResult)
    assert isinstance(result_be, IQParseResult)
    assert not np.array_equal(result_le.samples, result_be.samples)


def test_parse_float64_output_dtype_is_float32() -> None:
    """float64 input must be downcast to float32 (gap #8)."""
    values = [0.1, 0.2, 0.3, 0.4]  # 2 complex samples
    buffer = struct.pack(f"<{len(values)}d", *values)
    descriptor = make_descriptor(
        sample_format=SampleFormat.FLOAT64,
        dc_offset_remove=False,
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32
    assert result.sample_count == 2


def test_parse_float64_values_match_float32_downcast() -> None:
    """float64 values survive the downcast within float32 precision (gap #8)."""
    values = [0.1, -0.2, 0.5, -0.5]
    buffer = struct.pack(f"<{len(values)}d", *values)
    descriptor = make_descriptor(
        sample_format=SampleFormat.FLOAT64,
        dc_offset_remove=False,
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    expected = np.array(values, dtype=np.float32)
    np.testing.assert_array_almost_equal(result.samples, expected, decimal=6)


def test_parse_float32_big_endian_roundtrip_values_preserved() -> None:
    """Big-endian float32 path: byte order must flip correctly."""
    values = [0.1, -0.2, 0.3, -0.4]
    buffer = struct.pack(f">{len(values)}f", *values)
    descriptor = make_descriptor(
        endianness=Endianness.BIG, normalize=False, dc_offset_remove=False
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32
    assert result.sample_count == len(values) // 2
    np.testing.assert_array_almost_equal(
        result.samples, np.array(values, dtype=np.float32)
    )


def test_parse_skips_dc_offset_removal_when_disabled() -> None:
    """When dc_offset_remove=False, channel bias must survive unchanged."""
    n = 64
    i_ch = np.full(n, 0.3, dtype=np.float32)
    q_ch = np.full(n, -0.2, dtype=np.float32)
    buffer = interleave_float32(i_ch, q_ch)
    descriptor = make_descriptor(normalize=False, dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert abs(float(result.samples[0::2].mean()) - 0.3) < 1e-5
    assert abs(float(result.samples[1::2].mean()) - (-0.2)) < 1e-5


def test_parse_output_length_matches_sample_count_times_two() -> None:
    """Invariant: len(samples) == sample_count * 2."""
    n_samples = 16
    i_ch = np.zeros(n_samples, dtype=np.float32)
    q_ch = np.zeros(n_samples, dtype=np.float32)
    buffer = interleave_float32(i_ch, q_ch)
    descriptor = make_descriptor(dc_offset_remove=False)

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert len(result.samples) == result.sample_count * 2


def test_parse_float64_normalize_true_clips_to_unit_range() -> None:
    """float64 with normalize=True: out-of-range values must be clipped."""
    values = [2.0, -3.0, 0.5, -0.5]
    buffer = struct.pack(f"<{len(values)}d", *values)
    descriptor = make_descriptor(
        sample_format=SampleFormat.FLOAT64, normalize=True, dc_offset_remove=False
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)
    assert result.samples[0] == pytest.approx(1.0)   # 2.0 clipped
    assert result.samples[1] == pytest.approx(-1.0)  # -3.0 clipped


def test_parse_float64_normalize_false_preserves_out_of_range() -> None:
    """float64 with normalize=False: values outside [-1.0, 1.0] must pass through."""
    values = [2.0, -3.0, 0.5, -0.5]
    buffer = struct.pack(f"<{len(values)}d", *values)
    descriptor = make_descriptor(
        sample_format=SampleFormat.FLOAT64, normalize=False, dc_offset_remove=False
    )

    result = parse_iq(descriptor, buffer)

    assert isinstance(result, IQParseResult)
    expected = np.array(values, dtype=np.float32)
    np.testing.assert_array_almost_equal(result.samples, expected)


def test_parse_returns_unsupported_format_error_when_decode_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNSUPPORTED_FORMAT is reachable via _UnhandledFormatError (issue #2)."""
    import agent.processing.parse_iq as module

    def exploding_decode(*_: object) -> npt.NDArray[np.float32]:
        raise module._UnhandledFormatError("hypothetical new format")

    monkeypatch.setattr(module, "_decode_samples", exploding_decode)

    descriptor = make_descriptor(dc_offset_remove=False)
    result = parse_iq(descriptor, b"\x00" * 8)  # 8 bytes = 1 aligned float32 sample

    assert not isinstance(result, IQParseResult)
    assert result.code == IQParseErrorCode.UNSUPPORTED_FORMAT
