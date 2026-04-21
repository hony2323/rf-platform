"""Scripted fake server for integration tests.

Simulates the server-side WebSocket endpoint at the WebSocket-object level.
Injected into WebSocketTransport via the ws_connect hook.

Design contract:
  - One FakeServer == one fake WebSocket connection.
  - Pre-load server messages with push() before starting the session.
  - Inspect what the agent sent via the `received` property after the run.
  - No protocol logic — purely scripted input/output.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class _FakeWebSocket:
    """Minimal object returned by the fake ws_connect callable.

    Satisfies the interface that WebSocketTransport expects:
      - response_headers dict-like with "X-Session-Id"
      - async send(str)
      - async recv() -> str
      - async close()
    """

    def __init__(self, session_id: str) -> None:
        self.response_headers: dict[str, str] = {"X-Session-Id": session_id}
        self.received: list[str] = []
        self._inbound: asyncio.Queue[str | BaseException] = asyncio.Queue()
        self._send_error: Exception | None = None
        self._send_error_after: int = 0
        self._send_count: int = 0

    def push(self, msg: str) -> None:
        """Enqueue a text message for the agent to receive on the next recv()."""
        self._inbound.put_nowait(msg)

    def push_exception(self, exc: BaseException) -> None:
        """Cause the agent's next recv() to raise *exc*."""
        self._inbound.put_nowait(exc)

    def set_send_error(self, exc: Exception, *, after_n: int = 0) -> None:
        """Raise *exc* on send(), skipping the first *after_n* sends.

        Use after_n=N to let N sends succeed first (e.g. after_n=2 to pass
        the handshake's connect + stream_config before failing).
        """
        self._send_error = exc
        self._send_error_after = after_n
        self._send_count = 0

    async def send(self, msg: str) -> None:
        if self._send_error is not None:
            if self._send_count >= self._send_error_after:
                exc = self._send_error
                self._send_error = None
                self._send_count = 0
                raise exc
            self._send_count += 1
        self.received.append(msg)

    async def recv(self) -> str:
        item = await self._inbound.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        pass


class FakeServer:
    """Scripted fake WebSocket server for integration tests.

    Creates a single fake WebSocket connection.  Call make_ws_connect() to
    get a callable suitable for WebSocketTransport(ws_connect=...).

    Usage::

        server = FakeServer(session_id="ses-001")
        server.push(connect_ack_msg(server.session_id))
        server.push(stream_config_ack_msg(server.session_id, "default", 1))
        server.push(disconnect_msg(server.session_id))

        transport = WebSocketTransport(ws_connect=server.make_ws_connect())
        session = Session(config=cfg, transport=transport, codec=JsonBase64Codec())
    """

    def __init__(self, session_id: str = "test-session-id-001") -> None:
        self.session_id = session_id
        self._ws = _FakeWebSocket(session_id)

    @property
    def received(self) -> list[str]:
        """All messages sent by the agent to this server, in order."""
        return self._ws.received

    def push(self, msg: str) -> None:
        """Enqueue a message the agent will receive on its next recv()."""
        self._ws.push(msg)

    def push_exception(self, exc: BaseException) -> None:
        """Cause the agent's next recv() to raise *exc*."""
        self._ws.push_exception(exc)

    def set_send_error(self, exc: Exception, *, after_n: int = 0) -> None:
        """Raise *exc* on a future send(), skipping the first *after_n* sends.

        after_n=2 lets the handshake's connect + stream_config succeed, then
        fails on the first frame send.
        """
        self._ws.set_send_error(exc, after_n=after_n)

    def make_ws_connect(self) -> Callable[..., Any]:
        """Return a ws_connect coroutine for WebSocketTransport injection."""
        ws = self._ws

        async def _connect(
            url: str,
            *,
            additional_headers: dict[str, str],
            **_: Any,
        ) -> _FakeWebSocket:
            return ws

        return _connect
