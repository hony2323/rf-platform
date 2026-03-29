"""IQ source interface.

A source produces blocks of raw IQ bytes. It knows how to talk to hardware
(or a simulator), but knows nothing about FFT, sessions, or networking.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from agent.domain import IQDescriptor


class IQSource(Protocol):
    """Async producer of raw IQ sample blocks."""

    @property
    def descriptor(self) -> IQDescriptor:
        """The format descriptor for buffers this source produces."""
        ...

    async def start(self) -> None:
        """Initialize hardware / open file / start simulator."""
        ...

    async def stop(self) -> None:
        """Release hardware / close file."""
        ...

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        """Read IQ blocks and push them to `output` until stopped.

        Each item pushed is a raw bytes buffer matching self.descriptor.
        The caller owns the queue and its bounds.
        Raises asyncio.CancelledError on shutdown.
        """
        ...
