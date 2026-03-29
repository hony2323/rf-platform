"""FFT processing pipeline interface.

Consumes raw IQ blocks, produces SpectrumFrame objects.
Pure computation — no I/O, no network awareness.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from agent.domain import IQDescriptor, RFConfig, SpectrumFrame


class FFTProcessor(Protocol):
    """Transforms IQ samples into spectrum frames."""

    def configure(self, rf_config: RFConfig, descriptor: IQDescriptor) -> None:
        """Set FFT parameters. Can be called again on config change.

        Pre-computes window function, allocates output buffer.
        """
        ...

    def process(self, iq_bytes: bytes) -> SpectrumFrame:
        """Process a single block of IQ bytes into a SpectrumFrame.

        Steps (in order):
        1. Parse IQ bytes (via iq_parser) → normalized float32
        2. Apply window function (hann)
        3. FFT
        4. Compute power: 10 * log10(|X|² / N²) → dBFS
        5. fftshift (DC center → low_to_high bin order)
        6. Pack as float32 LE bytes → SpectrumFrame.payload

        Raises ValueError if iq_bytes length doesn't match fft_size.
        """
        ...

    async def run(
        self,
        input_queue: asyncio.Queue[bytes],
        output_queue: asyncio.Queue[SpectrumFrame],
    ) -> None:
        """Continuous processing loop.

        Reads IQ blocks from input_queue, calls process(), pushes
        SpectrumFrame to output_queue. Runs until cancelled.
        """
        ...
