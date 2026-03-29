"""WebSocket transport interface.

Dumb pipe with reconnect. Knows about WebSocket, TLS, and bearer token.
Does NOT understand protocol messages — just sends and receives strings/bytes.
"""

from __future__ import annotations

import enum
from typing import Protocol


class TransportState(enum.Enum):
    CLOSED = "closed"
    CONNECTING = "connecting"
    OPEN = "open"


class TransportEvent(enum.Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    MESSAGE = "message"
    ERROR = "error"


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

        Stores X-Session-Id from response headers.
        Raises ConnectionError on failure.
        """
        ...

    async def send(self, message: str | bytes) -> None:
        """Send a message over the open WebSocket.

        Raises ConnectionError if not connected.
        """
        ...

    async def recv(self) -> str | bytes:
        """Receive the next message from the WebSocket.

        Blocks until a message arrives.
        Raises ConnectionError if connection drops.
        """
        ...

    async def close(self) -> None:
        """Close the WebSocket gracefully."""
        ...
