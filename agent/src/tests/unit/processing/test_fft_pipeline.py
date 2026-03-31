"""Unit tests for FFTProcessor — wire contract.

Locks payload size, bin ordering, and timestamp pass-through.
Does not test private attributes or internal computation details.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt
import pytest

from agent.domain import RFConfig, SpectrumFrame, WindowFunction
from agent.processing.fft_pipeline import FFTProcessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_rf_config(
    *,
    fft_size: int,
    bin_count: int | None = None,
    sample_rate_hz: int = 2_400_000,
    center_freq_hz: int = 433_920_000,
) -> RFConfig:
    return RFConfig(
        center_freq_hz=center_freq_hz,
        sample_rate_hz=sample_rate_hz,
        fft_size=fft_size,
        window_fn=WindowFunction.HANN,
        bin_count=bin_count,
    )


def make_complex_tone_interleaved(
    sample_rate_hz: int,
    fft_size: int,
    f_tone_hz: float,
) -> npt.NDArray[np.float32]:
    """Return interleaved float32 IQ for a bin-aligned complex tone exp(j*2π*f*n/fs).

    Bin-aligned by design so the peak falls exactly on one FFT bin, with no
    spectral leakage that could shift argmax even through a Hann window.
    """
    n = np.arange(fft_size)
    tone = np.exp(1j * 2 * np.pi * f_tone_hz * n / sample_rate_hz)
    samples = np.empty(fft_size * 2, dtype=np.float32)
    samples[0::2] = tone.real.astype(np.float32)
    samples[1::2] = tone.imag.astype(np.float32)
    return samples


def unpack_payload(frame: SpectrumFrame) -> npt.NDArray[np.float32]:
    """Deserialize the wire payload to a float32 array."""
    return np.frombuffer(frame.payload, dtype=np.float32)


_TIMESTAMP = "2026-01-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fft_processor_requires_configure_before_process() -> None:
    """process() before configure() must raise — not silently return garbage."""
    processor = FFTProcessor()
    samples = np.zeros(16, dtype=np.float32)
    with pytest.raises(RuntimeError):
        processor.process(samples, _TIMESTAMP)


def test_fft_processor_output_payload_length_equals_bin_count_times_four() -> None:
    """Default bin_count == fft_size; payload length = bin_count * 4 bytes."""
    fft_size = 8
    processor = FFTProcessor()
    processor.configure(make_rf_config(fft_size=fft_size))

    samples = make_complex_tone_interleaved(8, fft_size, 1.0)
    frame = processor.process(samples, _TIMESTAMP)

    assert frame.bin_count == 8
    assert len(frame.payload) == 8 * 4


def test_fft_processor_output_payload_length_uses_explicit_bin_count_not_fft_size() -> (
    None
):
    """Explicit bin_count controls payload length, independently of fft_size."""
    fft_size = 16
    bin_count = 10
    processor = FFTProcessor()
    processor.configure(make_rf_config(fft_size=fft_size, bin_count=bin_count))

    samples = make_complex_tone_interleaved(16, fft_size, 1.0)
    frame = processor.process(samples, _TIMESTAMP)

    assert frame.bin_count == 10
    assert len(frame.payload) == 10 * 4


def test_fft_processor_produces_float32_payload_when_unpacked() -> None:
    """Wire payload unpacks to float32 with length == bin_count."""
    fft_size = 8
    processor = FFTProcessor()
    processor.configure(make_rf_config(fft_size=fft_size))

    samples = make_complex_tone_interleaved(8, fft_size, 1.0)
    frame = processor.process(samples, _TIMESTAMP)
    unpacked = unpack_payload(frame)

    assert unpacked.dtype == np.float32
    assert len(unpacked) == frame.bin_count


def test_fft_processor_outputs_bins_in_low_to_high_order_for_positive_tone() -> None:
    """Positive-frequency tone peaks at the correct fftshifted bin.

    fft_size=8, sample_rate=8 Hz → bin_size=1 Hz.
    After fftshift, indices 0..7 correspond to frequencies:
        [-4, -3, -2, -1, 0, +1, +2, +3] Hz
    Tone at +2 Hz → peak at index 6.
    """
    fft_size = 8
    sample_rate_hz = 8

    processor = FFTProcessor()
    processor.configure(
        make_rf_config(
            fft_size=fft_size, sample_rate_hz=sample_rate_hz, center_freq_hz=0
        )
    )

    samples = make_complex_tone_interleaved(sample_rate_hz, fft_size, f_tone_hz=2.0)
    frame = processor.process(samples, _TIMESTAMP)
    payload = unpack_payload(frame)

    assert int(np.argmax(payload)) == 6


def test_fft_processor_outputs_bins_in_low_to_high_order_for_negative_tone() -> None:
    """Negative-frequency tone peaks at the correct fftshifted bin.

    fft_size=8, sample_rate=8 Hz → bin_size=1 Hz.
    After fftshift, indices 0..7 correspond to frequencies:
        [-4, -3, -2, -1, 0, +1, +2, +3] Hz
    Tone at -2 Hz → peak at index 2.
    """
    fft_size = 8
    sample_rate_hz = 8

    processor = FFTProcessor()
    processor.configure(
        make_rf_config(
            fft_size=fft_size, sample_rate_hz=sample_rate_hz, center_freq_hz=0
        )
    )

    samples = make_complex_tone_interleaved(sample_rate_hz, fft_size, f_tone_hz=-2.0)
    frame = processor.process(samples, _TIMESTAMP)
    payload = unpack_payload(frame)

    assert int(np.argmax(payload)) == 2


def test_fft_processor_timestamp_is_passed_through_verbatim() -> None:
    """timestamp_utc must arrive in SpectrumFrame unchanged."""
    timestamp = "2026-03-31T12:34:56.789Z"
    processor = FFTProcessor()
    processor.configure(make_rf_config(fft_size=8))

    samples = make_complex_tone_interleaved(8, 8, 1.0)
    frame = processor.process(samples, timestamp)

    assert frame.timestamp_utc == timestamp


def test_fft_processor_reconfigure_changes_output_payload_shape_immediately() -> None:
    """configure() takes effect on the very next process() call."""
    processor = FFTProcessor()

    processor.configure(make_rf_config(fft_size=8, bin_count=8))
    frame_a = processor.process(make_complex_tone_interleaved(8, 8, 1.0), _TIMESTAMP)

    processor.configure(make_rf_config(fft_size=16, bin_count=12))
    frame_b = processor.process(make_complex_tone_interleaved(16, 16, 1.0), _TIMESTAMP)

    assert len(frame_a.payload) == 8 * 4
    assert len(frame_b.payload) == 12 * 4
    assert frame_b.bin_count == 12
