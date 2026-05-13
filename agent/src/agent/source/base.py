"""IQ source interface.

A source produces blocks of raw IQ bytes. It knows how to talk to hardware
(or a simulator), but knows nothing about FFT, sessions, or networking.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from agent.domain import IQDescriptor, RFConfig, TunerConfig


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


@runtime_checkable
class LiveRetunableSource(Protocol):
    """Optional capability: source supports live RF / tuner reconfiguration.

    Sources opt in by implementing `apply_rf_update`. Use `isinstance(src,
    LiveRetunableSource)` to dispatch; sources without the method are
    treated as non-tunable and a `config_request` against them is rejected.
    """

    async def apply_rf_update(self, rf: RFConfig, tuner: TunerConfig | None) -> None:
        """Apply a new RF / tuner config to the running source.

        Implementations must:
          - update any hardware-facing state (center_freq, sample_rate, gain)
          - replace `self._descriptor` with a fresh frozen `IQDescriptor`
            reflecting the new values
          - raise on any failure; the caller maps the exception to
            `CONFIG_REJECTED` on the wire
        """
        ...
