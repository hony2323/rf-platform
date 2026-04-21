"""Synthetic IQ simulator source — generates a pure tone, no hardware required."""

from __future__ import annotations

import asyncio
import math
import struct

from agent.domain import IQDescriptor


class SimulatorSource:
    """Generates synthetic float32 IQ blocks — no hardware required.

    Args:
        descriptor:       IQ format and frequency metadata.
        block_size:       Bytes per output block. Should be
                          ``fft_size * 8`` for smooth per-frame pacing.
        tone_offset_hz:   Tone offset from centre frequency. Default: 100 kHz.
        rate_limit_msps:  Throttle output to N Msps. None = unlimited.
    """

    def __init__(
        self,
        descriptor: IQDescriptor,
        block_size: int = 8_192,
        tone_offset_hz: float = 100_000.0,
        rate_limit_msps: float | None = None,
    ) -> None:
        from agent.domain import Endianness, Layout, SampleFormat

        if (
            descriptor.sample_format is not SampleFormat.FLOAT32
            or descriptor.endianness is not Endianness.LITTLE
            or descriptor.layout is not Layout.INTERLEAVED
        ):
            raise ValueError(
                "SimulatorSource only supports FLOAT32 + LITTLE + INTERLEAVED; "
                f"got sample_format={descriptor.sample_format.value!r}, "
                f"endianness={descriptor.endianness.value!r}, "
                f"layout={descriptor.layout.value!r}"
            )
        self._descriptor = descriptor
        self._block_size = block_size
        self._tone_offset_hz = tone_offset_hz
        self._phase = 0.0
        self._sleep_per_block: float | None = None
        if rate_limit_msps is not None:
            n_samples = block_size // 8  # float32 complex = 8 bytes/sample
            self._sleep_per_block = n_samples / (rate_limit_msps * 1e6)

    @property
    def descriptor(self) -> IQDescriptor:
        return self._descriptor

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        sr = self._descriptor.sample_rate_hz
        omega = 2.0 * math.pi * self._tone_offset_hz / sr
        n_samples = self._block_size // 8

        while True:
            floats: list[float] = []
            for _ in range(n_samples):
                floats.append(math.cos(self._phase))
                floats.append(math.sin(self._phase))
                self._phase = (self._phase + omega) % (2.0 * math.pi)
            await output.put(struct.pack(f"<{len(floats)}f", *floats))
            await asyncio.sleep(self._sleep_per_block or 0)
