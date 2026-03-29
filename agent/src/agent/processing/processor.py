"""IQ processing pipeline — parse_iq → sample accumulation → FFTProcessor.

Wires the stateless parse_iq function and FFTProcessor into a single
asyncio-compatible stage that:
  1. Prepends byte remainders across chunk boundaries.
  2. Accumulates float32 samples until fft_size complex samples are ready.
  3. Emits SpectrumFrames to an output queue.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import numpy as np
import numpy.typing as npt

from agent.domain import IQDescriptor, RFConfig, SpectrumFrame
from agent.processing.fft_pipeline import FFTProcessor
from agent.processing.parse_iq import IQParseError, parse_iq


class IQProcessor:
    """parse_iq → sample accumulation → FFTProcessor.

    Handles byte remainders at chunk boundaries and accumulates float32
    samples until fft_size complex samples are ready for the FFT stage.

    Not thread-safe. Designed for single-task asyncio use.
    """

    def __init__(self, descriptor: IQDescriptor, rf_config: RFConfig) -> None:
        self._descriptor = descriptor
        self._fft = FFTProcessor()
        self._fft.configure(rf_config)
        self._fft_size = rf_config.fft_size
        self._sample_buf: list[npt.NDArray[np.float32]] = []
        self._sample_count = 0
        self._remainder = b""
        self.parse_error_count: int = 0  # lifetime counter; never resets

    def configure(self, rf_config: RFConfig) -> None:
        """Replace the active RF/FFT config.

        Flushes the sample accumulation buffer and byte remainder so the
        new fft_size takes effect on the very next push() call.
        """
        self._fft.configure(rf_config)
        self._fft_size = rf_config.fft_size
        self._sample_buf.clear()
        self._sample_count = 0
        self._remainder = b""

    def push(self, chunk: bytes, timestamp_utc: str) -> list[SpectrumFrame]:
        """Feed one raw IQ byte chunk. Returns 0 or more SpectrumFrames.

        Prepends any leftover bytes from the previous call, aligns to a
        whole-sample boundary, and holds trailing bytes for the next call.
        parse_iq errors (e.g. EMPTY_BUFFER after alignment) are silently
        dropped — the caller is responsible for producing well-formed chunks.
        """
        data = self._remainder + chunk
        bps = self._descriptor.bytes_per_sample

        remainder_len = len(data) % bps
        if remainder_len:
            self._remainder = data[-remainder_len:]
            data = data[:-remainder_len]
        else:
            self._remainder = b""

        if not data:
            return []

        result = parse_iq(self._descriptor, data)
        if isinstance(result, IQParseError):
            # data is sample-aligned so this should not happen; increment counter
            # to make silent drops observable (e.g. for telemetry / debugging).
            self.parse_error_count += 1
            return []

        self._sample_buf.append(result.samples)
        self._sample_count += result.sample_count

        frames: list[SpectrumFrame] = []
        while self._sample_count >= self._fft_size:
            all_samples = np.concatenate(self._sample_buf)
            frame_samples = all_samples[: self._fft_size * 2]
            leftover = all_samples[self._fft_size * 2 :]
            self._sample_buf = [leftover] if len(leftover) > 0 else []
            self._sample_count -= self._fft_size
            frames.append(self._fft.process(frame_samples, timestamp_utc))

        return frames

    async def run(
        self,
        iq_queue: asyncio.Queue[bytes],
        frame_queue: asyncio.Queue[SpectrumFrame],
    ) -> None:
        """Drain iq_queue, push SpectrumFrames to frame_queue.

        Runs until cancelled. Timestamp is set to the moment each chunk
        is dequeued (proxy for capture time in the absence of hardware
        timestamping at MVP).
        """
        while True:
            chunk = await iq_queue.get()
            timestamp_utc = datetime.now(timezone.utc).isoformat()
            for frame in self.push(chunk, timestamp_utc):
                await frame_queue.put(frame)
