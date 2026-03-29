"""Unit tests for FFTProcessor."""

from __future__ import annotations

import math
import struct

import numpy as np
import pytest

from agent.domain import RFConfig, SpectrumFrame, WindowFunction
from agent.processing.fft_pipeline import FFTProcessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIMESTAMP = "2026-03-29T10:00:00.000Z"

# Use a sample rate that gives an exact integer bin size so tones are
# guaranteed bin-aligned — avoids Hann-window scalloping ambiguity in peak tests.
#   bin_size = 102400 / 1024 = 100 Hz exactly
_FFT_SIZE = 1024
_SAMPLE_RATE = 102_400


def make_config(**kwargs: object) -> RFConfig:
    defaults: dict[str, object] = {
        "center_freq_hz": 433_920_000,
        "sample_rate_hz": _SAMPLE_RATE,
        "fft_size": _FFT_SIZE,
        "window_fn": WindowFunction.HANN,
    }
    defaults.update(kwargs)
    return RFConfig(**defaults)  # type: ignore[arg-type]


def make_tone(f_hz: float, sample_rate: int, n_samples: int) -> np.ndarray:
    """Interleaved float32 IQ for a complex tone at f_hz, amplitude 0.5."""
    t = np.arange(n_samples) / sample_rate
    I = np.cos(2 * math.pi * f_hz * t).astype(np.float32) * 0.5
    Q = np.sin(2 * math.pi * f_hz * t).astype(np.float32) * 0.5
    buf = np.empty(n_samples * 2, dtype=np.float32)
    buf[0::2] = I
    buf[1::2] = Q
    return buf


def unpack_payload(frame: SpectrumFrame) -> np.ndarray:
    n = frame.bin_count
    return np.array(struct.unpack(f"<{n}f", frame.payload), dtype=np.float32)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fft_processor_requires_configure_before_process() -> None:
    proc = FFTProcessor()
    samples = make_tone(1000, _SAMPLE_RATE, _FFT_SIZE)
    with pytest.raises(RuntimeError):
        proc.process(samples, _TIMESTAMP)


def test_fft_processor_output_payload_length_equals_bin_count_times_four() -> None:
    proc = FFTProcessor()
    proc.configure(make_config())
    frame = proc.process(make_tone(1000, _SAMPLE_RATE, _FFT_SIZE), _TIMESTAMP)
    assert len(frame.payload) == frame.bin_count * 4
    assert frame.bin_count == _FFT_SIZE


def test_fft_processor_applies_hann_window_when_configured() -> None:
    """Output must match a manual reference computation using the same window."""
    config = make_config()
    samples = make_tone(1000, _SAMPLE_RATE, _FFT_SIZE)

    # Reference: manual Hann window + FFT + coherent-gain normalization + dBFS
    window = np.hanning(_FFT_SIZE)
    window_norm = float(np.sum(window))
    complex_in = samples[0::2].astype(np.float64) + 1j * samples[1::2].astype(np.float64)
    fft_out = np.fft.fft(complex_in * window)
    power_db_ref = (
        10.0 * np.log10(np.maximum((np.abs(fft_out) / window_norm) ** 2, 1e-12))
    ).astype(np.float32)
    reference_payload = np.fft.fftshift(power_db_ref)

    proc = FFTProcessor()
    proc.configure(config)
    frame = proc.process(samples, _TIMESTAMP)
    result = unpack_payload(frame)

    np.testing.assert_array_almost_equal(result, reference_payload, decimal=5)


def test_fft_processor_produces_log_power_float32_payload() -> None:
    proc = FFTProcessor()
    proc.configure(make_config())
    frame = proc.process(make_tone(1000, _SAMPLE_RATE, _FFT_SIZE), _TIMESTAMP)
    values = unpack_payload(frame)

    assert values.dtype == np.float32
    # Log-power values are all finite (no inf / nan from the floor clamp)
    assert np.all(np.isfinite(values))
    # A signal with amplitude 0.5 must produce at least one bin below 0 dBFS
    assert np.any(values < 0.0)


def test_fft_processor_outputs_bins_in_low_to_high_order() -> None:
    """Peak of a tone at +f must land at bin fft_size//2 + k, not fft_size//2 - k."""
    f_tone = 1_000     # Hz — bin-aligned: bin 10 from DC
    expected_bin = _FFT_SIZE // 2 + round(f_tone / (_SAMPLE_RATE / _FFT_SIZE))  # 522

    proc = FFTProcessor()
    proc.configure(make_config())
    frame = proc.process(make_tone(f_tone, _SAMPLE_RATE, _FFT_SIZE), _TIMESTAMP)
    values = unpack_payload(frame)

    peak_bin = int(np.argmax(values))
    assert peak_bin == expected_bin


def test_fft_processor_timestamp_is_capture_start_not_processing_end() -> None:
    proc = FFTProcessor()
    proc.configure(make_config())
    ts = "2026-01-15T08:30:00.000Z"
    frame = proc.process(make_tone(1000, _SAMPLE_RATE, _FFT_SIZE), ts)
    assert frame.timestamp_utc == ts


def test_fft_processor_reconfigure_changes_output_shape_and_resets_internal_state() -> None:
    proc = FFTProcessor()

    # First config: fft_size=512
    config_512 = make_config(fft_size=512)
    proc.configure(config_512)
    samples_512 = make_tone(1000, _SAMPLE_RATE, 512)
    frame_512 = proc.process(samples_512, _TIMESTAMP)
    assert frame_512.bin_count == 512
    assert len(frame_512.payload) == 512 * 4

    # Reconfigure: fft_size=1024 — new config must take effect immediately
    config_1024 = make_config(fft_size=1024)
    proc.configure(config_1024)
    samples_1024 = make_tone(1000, _SAMPLE_RATE, 1024)
    frame_1024 = proc.process(samples_1024, _TIMESTAMP)
    assert frame_1024.bin_count == 1024
    assert len(frame_1024.payload) == 1024 * 4


def test_fft_processor_rejects_wrong_sample_count() -> None:
    proc = FFTProcessor()
    proc.configure(make_config(fft_size=1024))
    wrong_samples = make_tone(1000, _SAMPLE_RATE, 512)  # half the expected count
    with pytest.raises(ValueError):
        proc.process(wrong_samples, _TIMESTAMP)
