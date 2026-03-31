"""Unit tests for WebSocketTransport.

All tests use FakeWebSocket — no real server, no real websockets library.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.transport import TransportState
from agent.transport.transport import WebSocketTransport

# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------

_DEFAULT_SESSION_ID = "ses_abc"
_URL = "ws://localhost:8000/ws"
_TOKEN = "test-token"


class FakeWebSocket:
    """Minimal fake for the underlying websocket connection object."""

    def __init__(self, session_id: str | None = _DEFAULT_SESSION_ID) -> None:
        self.response_headers: dict[str, str | None] = {}
        if session_id is not None:
            self.response_headers["X-Session-Id"] = session_id
        self.sent: list[str] = []
        self._recv_queue: asyncio.Queue[object] = asyncio.Queue()
        self.close_called = False
        self._send_error: Exception | None = None
        self._recv_error: Exception | None = None

    def push(self, msg: object) -> None:
        """Enqueue a message to be returned by recv()."""
        self._recv_queue.put_nowait(msg)

    async def send(self, msg: str) -> None:
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(msg)

    async def recv(self) -> object:
        if self._recv_error is not None:
            raise self._recv_error
        return await self._recv_queue.get()

    async def close(self) -> None:
        self.close_called = True


def _make_connect(
    fake_ws: FakeWebSocket,
    captured: list[dict[str, object]] | None = None,
) -> object:
    """Return an async callable that records call args and returns fake_ws."""

    async def _connect(url: str, additional_headers: dict[str, str] | None = None, **_: object) -> FakeWebSocket:
        if captured is not None:
            captured.append({"url": url, "additional_headers": additional_headers or {}})
        return fake_ws

    return _connect


def _make_failing_connect(exc: Exception) -> object:
    """Return an async callable that always raises exc."""

    async def _connect(*_: object, **__: object) -> FakeWebSocket:
        raise exc

    return _connect


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_transport_starts_closed() -> None:
    t = WebSocketTransport()
    assert t.state is TransportState.CLOSED
    assert t.session_id_from_header is None


async def test_transport_connect_uses_bearer_token_in_authorization_header() -> None:
    calls: list[dict[str, object]] = []
    fake_ws = FakeWebSocket()
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws, calls))

    await t.connect(_URL, _TOKEN)

    assert len(calls) == 1
    headers = calls[0]["additional_headers"]
    assert isinstance(headers, dict)
    assert headers.get("Authorization") == f"Bearer {_TOKEN}"


async def test_transport_extracts_session_id_from_upgrade_header() -> None:
    fake_ws = FakeWebSocket(session_id="ses_xyz")
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))

    await t.connect(_URL, _TOKEN)

    assert t.session_id_from_header == "ses_xyz"


async def test_transport_sets_state_open_after_successful_connect() -> None:
    fake_ws = FakeWebSocket()
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))

    await t.connect(_URL, _TOKEN)

    assert t.state is TransportState.OPEN


async def test_transport_connect_failure_leaves_state_closed() -> None:
    t = WebSocketTransport(ws_connect=_make_failing_connect(OSError("refused")))

    with pytest.raises(ConnectionError):
        await t.connect(_URL, _TOKEN)

    assert t.state is TransportState.CLOSED


async def test_transport_send_text_delegates_to_underlying_ws_client() -> None:
    fake_ws = FakeWebSocket()
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    await t.send('{"type":"connect"}')

    assert fake_ws.sent == ['{"type":"connect"}']


async def test_transport_send_raises_if_not_connected() -> None:
    t = WebSocketTransport()

    with pytest.raises(ConnectionError):
        await t.send("hello")


async def test_transport_recv_text_returns_text_message() -> None:
    fake_ws = FakeWebSocket()
    fake_ws.push('{"type":"connect_ack"}')
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    msg = await t.recv()

    assert msg == '{"type":"connect_ack"}'


async def test_transport_recv_raises_if_not_connected() -> None:
    t = WebSocketTransport()

    with pytest.raises(ConnectionError):
        await t.recv()


async def test_transport_recv_rejects_non_text_message() -> None:
    fake_ws = FakeWebSocket()
    fake_ws.push(b"\x00\x01\x02\x03")  # binary frame
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    with pytest.raises(TypeError, match="text"):
        await t.recv()


async def test_transport_close_closes_underlying_ws_client() -> None:
    fake_ws = FakeWebSocket()
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    await t.close()

    assert fake_ws.close_called


async def test_transport_close_is_idempotent() -> None:
    fake_ws = FakeWebSocket()
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    await t.close()
    await t.close()  # must not raise

    assert t.state is TransportState.CLOSED


async def test_transport_close_clears_underlying_connection_reference() -> None:
    fake_ws = FakeWebSocket()
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    await t.close()

    # After close, send/recv must fail (connection cleared)
    with pytest.raises(ConnectionError):
        await t.send("anything")


async def test_transport_close_clears_session_id() -> None:
    fake_ws = FakeWebSocket(session_id="ses_abc")
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)
    assert t.session_id_from_header == "ses_abc"

    await t.close()

    assert t.session_id_from_header is None


async def test_transport_send_failure_raises_connection_error() -> None:
    fake_ws = FakeWebSocket()
    fake_ws._send_error = OSError("broken pipe")
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    with pytest.raises(ConnectionError):
        await t.send("hello")


async def test_transport_recv_failure_raises_connection_error() -> None:
    fake_ws = FakeWebSocket()
    fake_ws._recv_error = OSError("connection reset")
    t = WebSocketTransport(ws_connect=_make_connect(fake_ws))
    await t.connect(_URL, _TOKEN)

    with pytest.raises(ConnectionError):
        await t.recv()


async def test_transport_new_connect_replaces_old_session_id() -> None:
    first_ws = FakeWebSocket(session_id="ses_first")
    second_ws = FakeWebSocket(session_id="ses_second")

    connect_calls = 0

    async def _connect(url: str, additional_headers: dict[str, str] | None = None, **_: object) -> FakeWebSocket:
        nonlocal connect_calls
        connect_calls += 1
        return first_ws if connect_calls == 1 else second_ws

    t = WebSocketTransport(ws_connect=_connect)

    await t.connect(_URL, _TOKEN)
    assert t.session_id_from_header == "ses_first"

    await t.close()
    await t.connect(_URL, _TOKEN)

    assert t.session_id_from_header == "ses_second"
