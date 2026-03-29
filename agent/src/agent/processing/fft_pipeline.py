"""FFT processing pipeline.

Input:  float32 interleaved IQ samples (output of parse_iq)
Output: SpectrumFrame — float32 log-power payload, bin_order=low_to_high, dBFS
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from agent.domain import RFConfig, SpectrumFrame, WindowFunction


class FFTProcessor:
    """Windowed FFT → log-power → SpectrumFrame.

    Call configure() before the first process(). Call configure() again
    whenever RF or FFT parameters change — takes effect immediately.
    """

    def __init__(self) -> None:
        self._config: RFConfig | None = None
        self._window: npt.NDArray[np.float64] | None = None
        self._window_norm: float = 1.0

    def configure(self, config: RFConfig) -> None:
        """Set or replace the active RF/FFT configuration."""
        self._config = config
        self._window = _make_window(config.window_fn, config.fft_size)
        self._window_norm = float(np.sum(self._window))

    def process(
        self,
        samples: npt.NDArray[np.float32],
        timestamp_utc: str,
    ) -> SpectrumFrame:
        """Process exactly fft_size complex samples into a SpectrumFrame.

        Args:
            samples:       Interleaved float32 IQ array, len == fft_size * 2.
                           Must be normalized to [-1.0, 1.0] (parse_iq contract).
            timestamp_utc: ISO 8601 capture start time. Passed through verbatim.

        Returns:
            SpectrumFrame with float32 LE payload of length fft_size * 4.
            Payload values are dBFS log-power, bin_order=low_to_high.
        """
        if self._config is None or self._window is None:
            raise RuntimeError("configure() must be called before process()")

        config = self._config

        expected_len = config.fft_size * 2
        if len(samples) != expected_len:
            raise ValueError(
                f"expected {expected_len} samples (fft_size={config.fft_size} × 2), "
                f"got {len(samples)}"
            )

        # float64 throughout for FFT numerical precision; cast to float32 at output
        i_f64 = samples[0::2].astype(np.float64)
        q_f64 = samples[1::2].astype(np.float64)
        complex_in = i_f64 + 1j * q_f64

        windowed = complex_in * self._window
        fft_out = np.fft.fft(windowed)

        # Normalize by coherent window gain → power relative to full scale (dBFS)
        power = (np.abs(fft_out) / self._window_norm) ** 2
        power_shifted = np.fft.fftshift(power)

        # Clamp before log to avoid -inf; floor of -120 dB is well below noise
        power_db = (10.0 * np.log10(np.maximum(power_shifted, 1e-12))).astype(
            np.float32
        )

        return SpectrumFrame(
            payload=power_db.tobytes(),
            timestamp_utc=timestamp_utc,
            bin_count=config.fft_size,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_window(window_fn: WindowFunction, size: int) -> npt.NDArray[np.float64]:
    if window_fn == WindowFunction.HANN:
        return np.hanning(size)
    raise ValueError(f"unsupported window function: {window_fn}")
