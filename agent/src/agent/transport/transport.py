"""Concrete WebSocket transport implementation.

Thin adapter: WebSocket lifecycle, bearer auth, session_id extraction.
No protocol parsing, no retry logic, no RF/FFT knowledge.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import websockets
import websockets.asyncio.client

from agent.transport import TransportState


class WebSocketTransport:
    """WebSocket transport backed by the ``websockets`` library.

    Parameters
    ----------
    ws_connect:
        Callable with the same signature as ``websockets.connect``.
        Override in tests to inject a fake without a real server.
    """

    def __init__(
        self,
        ws_connect: Callable[..., Coroutine[Any, Any, Any]] | None = None,
    ) -> None:
        self._ws_connect: Callable[..., Coroutine[Any, Any, Any]] = (
            ws_connect if ws_connect is not None else websockets.connect  # type: ignore[assignment]
        )
        self._ws: Any = None
        self._state = TransportState.CLOSED
        self._session_id: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> TransportState:
        return self._state

    @property
    def session_id_from_header(self) -> str | None:
        return self._session_id

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, url: str, token: str) -> None:
        """Open WebSocket, send Bearer token, capture X-Session-Id.

        On failure the state stays CLOSED and the exception is re-raised
        as a ``ConnectionError``.
        """
        # Clear stale session_id before every connect attempt.
        self._session_id = None

        headers = {"Authorization": f"Bearer {token}"}
        try:
            ws = await self._ws_connect(url, additional_headers=headers)
        except Exception as exc:
            self._ws = None
            self._state = TransportState.CLOSED
            raise ConnectionError(f"WebSocket connect failed: {exc}") from exc

        self._ws = ws
        # websockets >= 13 asyncio API: headers live on ws.response.headers.
        response_headers = getattr(ws, "response", None)
        if response_headers is not None:
            self._session_id = response_headers.headers.get("X-Session-Id")
        else:
            # Fallback for scripted fakes that expose response_headers directly.
            self._session_id = getattr(ws, "response_headers", {}).get("X-Session-Id")
        self._state = TransportState.OPEN

    async def send(self, message: str) -> None:
        """Send a text message over the open connection.

        Raises ``ConnectionError`` if the transport is not open or send fails.
        """
        if self._ws is None or self._state is not TransportState.OPEN:
            raise ConnectionError("Transport is not connected")
        try:
            await self._ws.send(message)
        except Exception as exc:
            raise ConnectionError(f"WebSocket send failed: {exc}") from exc

    async def recv(self) -> str:
        """Receive the next text message from the connection.

        Raises
        ------
        ConnectionError
            If the transport is not open.
        TypeError
            If a non-text (binary) message arrives.
        """
        if self._ws is None or self._state is not TransportState.OPEN:
            raise ConnectionError("Transport is not connected")
        try:
            msg = await self._ws.recv()
        except Exception as exc:
            raise ConnectionError(f"WebSocket recv failed: {exc}") from exc
        if not isinstance(msg, str):
            raise TypeError(
                f"Expected text frame, got {type(msg).__name__}. "
                "Binary frames are not supported in MVP."
            )
        return msg

    async def close(self) -> None:
        """Close the connection gracefully. Safe to call multiple times."""
        if self._ws is None:
            return
        ws = self._ws
        self._ws = None
        self._state = TransportState.CLOSED
        self._session_id = None
        await ws.close()
