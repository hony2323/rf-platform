"""Outbound bandwidth limiters for the WebSocket send loop.

Each limiter implements `should_send(n_bytes) -> bool`. The send loop
calls it after encoding; if it returns False the frame is dropped and
local_throttle is incremented.

Important: limiting happens after encoding, so encoding CPU is always
spent regardless of whether the frame is sent. This saves bandwidth,
not CPU. If encoding CPU matters, drop frames before encoding instead.

Adding a new strategy:
  1. Implement a class with `should_send(self, n_bytes: int) -> bool`.
  2. Add a branch in `make_limiter`.
  3. Extend BandwidthConfig.strategy in config/__init__.py.
  No changes to Session._send_loop are needed.
"""

from __future__ import annotations

import asyncio
from typing import Protocol


class BandwidthLimiter(Protocol):
    def should_send(self, n_bytes: int) -> bool:
        """Return True if the frame should be sent, False if it should be dropped."""
        ...


class DecimateLimiter:
    """Rate-limit by dropping frames that arrive too early.

    On each call, derives the minimum inter-frame interval from the
    current frame's wire size and the byte budget. The first frame in
    each interval window is sent; frames that arrive before the window
    expires are dropped.

    Semantics to be aware of:
    - This is a dropper, not a pacer. Frames are not delayed or
      buffered — they are either sent immediately or discarded.
      Back-pressure is not applied to the pipeline.
    - The interval is recomputed from each frame's actual n_bytes,
      so it adapts automatically if frame sizes change (e.g. after
      an fft_size or bin_count change mid-session).
    - Send cadence is "at most one frame per interval". Actual
      throughput may be slightly below the cap because the interval
      is measured from the moment of the last send, not a fixed clock.
    """

    def __init__(self, max_bytes_per_sec: int) -> None:
        self._max_bytes_per_sec = max_bytes_per_sec
        self._next_send_at: float = 0.0
        self._initialized: bool = False

    def should_send(self, n_bytes: int) -> bool:
        now = asyncio.get_event_loop().time()
        # Interval is derived from this frame's size, so it adapts to
        # size changes without any special-casing.
        interval = n_bytes / self._max_bytes_per_sec
        if not self._initialized:
            # Allow the first frame through immediately and start the clock.
            self._next_send_at = now
            self._initialized = True
        if now < self._next_send_at:
            return False
        self._next_send_at = now + interval
        return True


class DropLimiter:
    """Token-bucket: send greedily until the byte budget is exhausted.

    Tokens accumulate continuously at max_bytes_per_sec bytes per second.
    Each sent frame deducts its wire size. Frames that find the bucket
    below their size are dropped immediately.

    Burst semantics: the bucket starts full (max_bytes_per_sec tokens),
    so a burst of up to one second's worth of data can be sent at line
    rate before throttling begins. This is standard token-bucket
    behaviour and is intentional — it allows brief traffic spikes while
    enforcing the average rate over time.
    """

    def __init__(self, max_bytes_per_sec: int) -> None:
        self._max_bytes_per_sec = max_bytes_per_sec
        self._bucket: float = float(max_bytes_per_sec)  # start full
        self._last_refill: float | None = None

    def should_send(self, n_bytes: int) -> bool:
        now = asyncio.get_event_loop().time()
        if self._last_refill is None:
            self._last_refill = now
        elapsed = now - self._last_refill
        self._bucket = min(
            float(self._max_bytes_per_sec),
            self._bucket + elapsed * self._max_bytes_per_sec,
        )
        self._last_refill = now
        if self._bucket < n_bytes:
            return False
        self._bucket -= n_bytes
        return True


def make_limiter(
    max_bytes_per_sec: int | None, strategy: str
) -> BandwidthLimiter | None:
    """Return the appropriate limiter, or None if bandwidth is unlimited."""
    if max_bytes_per_sec is None:
        return None
    if strategy == "decimate":
        return DecimateLimiter(max_bytes_per_sec)
    if strategy == "drop":
        return DropLimiter(max_bytes_per_sec)
    raise ValueError(f"Unknown bandwidth strategy: {strategy!r}")
