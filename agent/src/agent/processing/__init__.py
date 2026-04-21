"""FFT processing pipeline interface.

Consumes raw IQ byte blocks, produces SpectrumFrame objects.
Pure computation — no I/O, no network awareness.

Concrete implementations:
  IQProcessor   — full pipeline: parse_iq + sample accumulation + FFTProcessor
  FFTProcessor  — FFT stage only (windowed FFT → log-power → SpectrumFrame)
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from agent.domain import IQDescriptor, RFConfig, SpectrumFrame


class Processor(Protocol):
    """Pipeline stage: raw IQ bytes → SpectrumFrames."""

    def __init__(self, descriptor: IQDescriptor, rf_config: RFConfig) -> None: ...

    def configure(self, rf_config: RFConfig) -> None:
        """Replace the active RF/FFT config. Takes effect on the next push()."""
        ...

    def push(self, chunk: bytes, timestamp_utc: str) -> list[SpectrumFrame]:
        """Feed one raw IQ byte chunk. Returns 0 or more SpectrumFrames."""
        ...

    async def run(
        self,
        iq_queue: asyncio.Queue[bytes],
        frame_queue: asyncio.Queue[SpectrumFrame],
    ) -> None:
        """Drain iq_queue, push SpectrumFrames to frame_queue. Runs until cancelled."""
        ...
