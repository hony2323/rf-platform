"""RTL-SDR hardware source.

Reads IQ samples from a real-time RTL-SDR device via pyrtlsdr.
Converts the device's complex64 output to float32 interleaved bytes
(I0, Q0, I1, Q1, …) suitable for parse_iq.

Requires the 'sdr' optional dependency group:
    pip install -e ".[sdr]"
"""

from __future__ import annotations

import asyncio
import ctypes
from collections.abc import Callable
from typing import Any

import numpy as np

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat


# pyrtlsdr 0.4.0 resolves several optional symbols (dithering, GPIO control)
# at import time, but they are missing from the librtlsdr shipped by
# Debian/Ubuntu (2.0.2). None of them are used by this agent, so we install
# no-op stubs so the import succeeds. Has no effect on builds whose librtlsdr
# already exports the symbols.
_MISSING_LIBRTLSDR_SYMBOLS = frozenset(
    {
        "rtlsdr_set_dithering",
        "rtlsdr_set_gpio_output",
        "rtlsdr_set_gpio_input",
        "rtlsdr_set_gpio_bit",
        "rtlsdr_get_gpio_bit",
        "rtlsdr_set_gpio_byte",
        "rtlsdr_get_gpio_byte",
        "rtlsdr_set_gpio_status",
    }
)


def _install_missing_symbol_stubs() -> None:
    if getattr(ctypes.CDLL, "_rf_agent_patched", False):
        return

    _original_getattr = ctypes.CDLL.__getattr__

    def _patched_getattr(self: ctypes.CDLL, name: str) -> Any:
        try:
            return _original_getattr(self, name)
        except AttributeError:
            if name not in _MISSING_LIBRTLSDR_SYMBOLS:
                raise
            stub = ctypes.CFUNCTYPE(ctypes.c_int)(lambda *_: 0)
            setattr(self, name, stub)
            return stub

    ctypes.CDLL.__getattr__ = _patched_getattr  # type: ignore[method-assign]
    ctypes.CDLL._rf_agent_patched = True  # type: ignore[attr-defined]


class RTLSDRSource:
    """IQSource backed by a real-time RTL-SDR device.

    Args:
        center_freq_hz:  RF centre frequency in Hz.
        sample_rate_hz:  Sample rate in Hz.
        device_index:    RTL-SDR device index (0 = first found).
        gain:            Tuner gain. ``"auto"`` enables AGC; a float sets
                         manual gain in dB (driver units — see pyrtlsdr docs).
        chunk_samples:   Complex samples per read call. Default: 8192.
        fps:             Target chunks per second. The hardware always streams
                         at ``sample_rate_hz``; we sleep between reads so the
                         agent emits ~``fps`` chunks/sec. Hardware buffer
                         overruns are expected and harmless — we resync on
                         each read. Default: 10.
        _sdr_factory:    Optional callable ``(device_index: int) → sdr_obj``.
                         Injected in tests to replace real hardware.
    """

    def __init__(
        self,
        center_freq_hz: int,
        sample_rate_hz: int,
        device_index: int = 0,
        gain: str | float = "auto",
        chunk_samples: int = 8192,
        fps: float = 10.0,
        _sdr_factory: Callable[[int], Any] | None = None,
    ) -> None:
        self._center_freq_hz = center_freq_hz
        self._sample_rate_hz = sample_rate_hz
        self._device_index = device_index
        self._gain = gain
        self._chunk_samples = chunk_samples
        self._fps = fps
        self._sdr_factory = _sdr_factory
        self._sdr: Any | None = None
        self._descriptor = IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=center_freq_hz,
            normalize=True,
            dc_offset_remove=True,
        )

    @property
    def descriptor(self) -> IQDescriptor:
        return self._descriptor

    async def start(self) -> None:
        """Open and configure the RTL-SDR device."""
        if self._sdr_factory is not None:
            sdr = self._sdr_factory(self._device_index)
        else:
            try:
                _install_missing_symbol_stubs()
                from rtlsdr import RtlSdr
            except ImportError as exc:
                raise ImportError(
                    "pyrtlsdr is required for RTL-SDR support. "
                    "Install it with: pip install -e '.[sdr]'"
                ) from exc
            sdr = RtlSdr(self._device_index)

        sdr.sample_rate = self._sample_rate_hz
        sdr.center_freq = self._center_freq_hz
        if self._gain == "auto":
            sdr.gain = "auto"
        else:
            sdr.gain = float(self._gain)

        self._sdr = sdr

    async def stop(self) -> None:
        """Close the RTL-SDR device."""
        if self._sdr is not None:
            self._sdr.close()
            self._sdr = None

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        """Read chunks from the device and push float32 interleaved bytes to output.

        pyrtlsdr returns complex values already in the [-1.0, 1.0] range.
        We convert to complex64 then reinterpret as float32 to get the
        canonical [I0, Q0, I1, Q1, …] layout.

        Raises asyncio.CancelledError on cancellation.
        """
        if self._sdr is None:
            raise RuntimeError("call start() before run()")

        loop = asyncio.get_running_loop()
        sdr = self._sdr
        chunk = self._chunk_samples
        sleep_per_chunk = 1.0 / self._fps if self._fps > 0 else 0.0

        while True:
            complex_samples = await loop.run_in_executor(None, sdr.read_samples, chunk)
            arr = np.asarray(complex_samples, dtype=np.complex64)
            await output.put(arr.view(np.float32).tobytes())
            if sleep_per_chunk > 0:
                await asyncio.sleep(sleep_per_chunk)
