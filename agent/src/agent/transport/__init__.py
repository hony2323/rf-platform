"""WebSocket transport interface.

Thin WebSocket wrapper: bearer auth on HTTP Upgrade, X-Session-Id extraction
from the 101 response, raw text send/recv, and open/closed state.

Does NOT own: protocol parsing, session handshake, retry/backoff, RF/FFT.
"""

from __future__ import annotations

import enum
from typing import Protocol


class TransportState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"


class Transport(Protocol):
    """Async WebSocket transport."""

    @property
    def state(self) -> TransportState:
        """Current connection state."""
        ...

    @property
    def session_id_from_header(self) -> str | None:
        """The X-Session-Id from the HTTP 101 response, if connected."""
        ...

    async def connect(self, url: str, token: str) -> None:
        """Open WebSocket with Authorization: Bearer <token>.

        Captures X-Session-Id from the upgrade response headers.
        Raises ConnectionError on failure; state remains CLOSED.
        """
        ...

    async def send(self, message: str) -> None:
        """Send a text message over the open connection.

        Raises ConnectionError if not connected.
        """
        ...

    async def recv(self) -> str:
        """Receive the next text message from the connection.

        Raises ConnectionError if not connected or connection drops.
        Raises TypeError if a binary frame arrives.
        """
        ...

    async def close(self) -> None:
        """Close the connection gracefully. Safe to call multiple times."""
        ...
