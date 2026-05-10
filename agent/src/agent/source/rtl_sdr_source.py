"""RTL-SDR hardware source.

Reads IQ samples from a real-time RTL-SDR device via pyrtlsdr.
Converts the device's complex64 output to float32 interleaved bytes
(I0, Q0, I1, Q1, …) suitable for parse_iq.

Requires the 'sdr' optional dependency group:
    pip install -e ".[sdr]"
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import numpy as np

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat


class RTLSDRSource:
    """IQSource backed by a real-time RTL-SDR device.

    Args:
        center_freq_hz:  RF centre frequency in Hz.
        sample_rate_hz:  Sample rate in Hz.
        device_index:    RTL-SDR device index (0 = first found).
        gain:            Tuner gain. ``"auto"`` enables AGC; a float sets
                         manual gain in dB (driver units — see pyrtlsdr docs).
        chunk_samples:   Complex samples per read call. Default: 8192.
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
        _sdr_factory: Callable[[int], Any] | None = None,
    ) -> None:
        self._center_freq_hz = center_freq_hz
        self._sample_rate_hz = sample_rate_hz
        self._device_index = device_index
        self._gain = gain
        self._chunk_samples = chunk_samples
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
                from rtlsdr import RtlSdr  # type: ignore[import-not-found]
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

        while True:
            complex_samples = await loop.run_in_executor(None, sdr.read_samples, chunk)
            arr = np.asarray(complex_samples, dtype=np.complex64)
            await output.put(arr.view(np.float32).tobytes())
