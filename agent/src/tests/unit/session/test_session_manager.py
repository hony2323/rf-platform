"""Unit tests for the Session state machine.

Uses FakeTransport + FakeCodec — no real WebSocket, no real codec.
FakeCodec works entirely with dicts (encode returns dicts, decode
converts dicts to typed domain objects).

Runtime invalid-ack policy: FAIL the session.
An unexpected or malformed ack during streaming is treated as a protocol
violation that stops the session rather than being silently ignored.
"""

from __future__ import annotations

import asyncio

import pytest

from agent.config import AgentConfig, AgentIdentity, ServerConfig
from agent.domain import (
    ConnectionState,
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    SpectrumFrame,
    WireEncoding,
)
from agent.protocol import ConnectAck, Disconnect, ServerError, StreamConfigAck
from agent.session import Session, SessionError
from agent.transport import TransportState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SESSION_ID = "ses_1"
_STREAM_ID = "default"
_TIMESTAMP = "2026-01-01T00:00:00.000Z"

# ---------------------------------------------------------------------------
# FakeTransport
# ---------------------------------------------------------------------------


class FakeTransport:
    """In-memory transport. send() captures messages; recv() drains a queue."""

    def __init__(self, session_id: str | None = _SESSION_ID) -> None:
        self._state = TransportState.CLOSED
        self.session_id_from_header: str | None = session_id
        self._inbound: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[object] = []
        self._send_error: Exception | None = None

    @property
    def state(self) -> TransportState:
        return self._state

    async def connect(self, url: str, token: str) -> None:
        self._state = TransportState.OPEN

    async def send(self, msg: object) -> None:
        if self._send_error is not None:
            exc = self._send_error
            self._send_error = None
            raise exc
        self.sent.append(msg)

    async def recv(self) -> object:
        item = await self._inbound.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self._state = TransportState.CLOSED

    def queue_inbound(self, msg: object) -> None:
        """Push a server message into the receive queue."""
        self._inbound.put_nowait(msg)

    def push_error(self, exc: Exception) -> None:
        """Simulate a transport-level recv() failure."""
        self._inbound.put_nowait(exc)

    def set_send_error(self, exc: Exception) -> None:
        """Make the next send() raise exc."""
        self._send_error = exc


# ---------------------------------------------------------------------------
# FakeCodec
# ---------------------------------------------------------------------------


class FakeCodec:
    """Minimal codec for session tests.

    encode_* returns dicts (stored verbatim in FakeTransport.sent).
    decode converts dict → typed protocol object.
    Parses wire_encoding from the dict; raises ValueError for unknown values.
    """

    def encode_connect(self, *args: object, **kwargs: object) -> dict:
        return {"msg_type": "connect"}

    def encode_stream_config(self, *args: object, **kwargs: object) -> dict:
        return {"msg_type": "stream_config"}

    def encode_spectrum_frame(
        self,
        node_id: str,
        session_id: str | None,
        stream_id: str,
        config_version: int | None,
        frame_index: int,
        frame: SpectrumFrame,
    ) -> dict:
        return {
            "msg_type": "spectrum_frame",
            "frame_index": frame_index,
            "config_version": config_version,
        }

    def encode_heartbeat(self, *args: object, **kwargs: object) -> dict:
        return {"msg_type": "heartbeat"}

    def encode_agent_status(self, *args: object, **kwargs: object) -> dict:
        return {"msg_type": "agent_status"}

    def decode(self, raw: object) -> object:
        if not isinstance(raw, dict):
            raise ValueError(f"FakeCodec expects a dict, got {type(raw).__name__!r}")
        t = raw.get("msg_type")
        if t == "connect_ack":
            enc_str = raw.get("wire_encoding", "json_base64")
            try:
                enc = WireEncoding(enc_str)
            except ValueError as exc:
                raise ValueError(f"Unknown wire_encoding: {enc_str!r}") from exc
            return ConnectAck(
                session_id=raw["session_id"],
                status=raw["status"],
                wire_encoding=enc,
            )
        if t == "stream_config_ack":
            return StreamConfigAck(
                session_id=raw["session_id"],
                stream_id=raw["stream_id"],
                config_version=raw["config_version"],
                status=raw["status"],
            )
        if t == "error":
            return ServerError(
                session_id=raw["session_id"],
                code=raw["code"],
                message=raw["message"],
                fatal=raw["fatal"],
            )
        if t == "disconnect":
            return Disconnect(
                session_id=raw["session_id"],
                reason=raw["reason"],
            )
        raise ValueError(f"Unknown msg_type: {t!r}")


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def make_session(
    transport: FakeTransport | None = None,
    codec: FakeCodec | None = None,
) -> Session:
    if transport is None:
        transport = FakeTransport()
    if codec is None:
        codec = FakeCodec()
    config = AgentConfig(
        identity=AgentIdentity(node_id="node_test"),
        server=ServerConfig(url="wss://test", token="test-token"),
        rf=RFConfig(
            center_freq_hz=100_000_000,
            sample_rate_hz=1_000_000,
            fft_size=4,
        ),
        iq=IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=1_000_000,
            center_freq_hz=100_000_000,
        ),
    )
    return Session(config=config, transport=transport, codec=codec)


def make_frame(bin_count: int = 4) -> SpectrumFrame:
    return SpectrumFrame(
        payload=bytes(bin_count * 4),
        timestamp_utc=_TIMESTAMP,
        bin_count=bin_count,
    )


def make_connect_ack(
    session_id: str = _SESSION_ID,
    status: str = "ok",
    wire_encoding: str = "json_base64",
) -> dict:
    return {
        "msg_type": "connect_ack",
        "session_id": session_id,
        "status": status,
        "wire_encoding": wire_encoding,
    }


def make_stream_config_ack(
    session_id: str = _SESSION_ID,
    stream_id: str = _STREAM_ID,
    config_version: int = 1,
    status: str = "ok",
) -> dict:
    return {
        "msg_type": "stream_config_ack",
        "session_id": session_id,
        "stream_id": stream_id,
        "config_version": config_version,
        "status": status,
    }


def make_error(fatal: bool, session_id: str = _SESSION_ID) -> dict:
    return {
        "msg_type": "error",
        "session_id": session_id,
        "code": "INVALID_FRAME",
        "message": "bad frame",
        "fatal": fatal,
    }


def make_disconnect(session_id: str = _SESSION_ID) -> dict:
    return {
        "msg_type": "disconnect",
        "session_id": session_id,
        "reason": "server_shutdown",
    }


async def run_session_until_done(
    session: Session,
    frame_queue: asyncio.Queue,
    timeout: float = 0.1,
) -> None:
    """Run the session task; cancel after timeout if still running.

    Only suppresses CancelledError (external cancel) and SessionError
    (expected protocol failures). All other exceptions propagate so that
    test bugs are not hidden.
    """
    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(timeout)
    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, SessionError):
        pass


# ---------------------------------------------------------------------------
# 1. Initial state
# ---------------------------------------------------------------------------


def test_session_starts_in_disconnected_state() -> None:
    session = make_session()
    assert session.state == ConnectionState.DISCONNECTED


# ---------------------------------------------------------------------------
# 2. Handshake — happy path
# ---------------------------------------------------------------------------


async def test_session_state_is_connecting_while_waiting_for_connect_ack() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)  # session is blocked on recv() for connect_ack

    assert session.state == ConnectionState.CONNECTING

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_sends_connect_as_first_message() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)

    assert transport.sent[0]["msg_type"] == "connect"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_stores_session_id_from_transport_header() -> None:
    transport = FakeTransport(session_id="ses_xyz")
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)

    assert session.session_id == "ses_xyz"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_moves_to_connected_after_valid_connect_ack() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)

    assert session.state == ConnectionState.CONNECTED

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_sends_stream_config_as_second_message() -> None:
    """stream_config must be the second outbound message, after connect."""
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)

    assert transport.sent[0]["msg_type"] == "connect"
    assert transport.sent[1]["msg_type"] == "stream_config"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_moves_to_streaming_after_full_handshake() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)

    assert session.state == ConnectionState.STREAMING

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_stores_config_version_from_stream_config_ack() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(config_version=7))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)

    assert session.config_version == 7

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# 3. Handshake — failure cases
# ---------------------------------------------------------------------------


async def test_session_fails_if_no_session_id_in_header() -> None:
    transport = FakeTransport(session_id=None)
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_connect_ack_session_id_mismatch() -> None:
    transport = FakeTransport(session_id=_SESSION_ID)
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(session_id="WRONG"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_connect_ack_status_not_ok() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(status="rejected"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_connect_ack_wire_encoding_mismatch() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    # "msgpack" is not in WireEncoding → codec raises ValueError → SessionError
    transport.queue_inbound(make_connect_ack(wire_encoding="msgpack"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_stream_config_ack_session_id_mismatch() -> None:
    transport = FakeTransport(session_id=_SESSION_ID)
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(session_id="WRONG"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_stream_config_ack_stream_id_mismatch() -> None:
    transport = FakeTransport(session_id=_SESSION_ID)
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(stream_id="WRONG"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_stream_config_ack_status_not_ok() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(status="error"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


# ---------------------------------------------------------------------------
# 4. Frame gating
# ---------------------------------------------------------------------------


async def test_no_spectrum_frame_sent_before_stream_config_ack() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    await frame_queue.put(make_frame())

    # Only push connect_ack — session blocks waiting for stream_config_ack
    transport.queue_inbound(make_connect_ack())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    sent_types = [m["msg_type"] for m in transport.sent]
    assert "spectrum_frame" not in sent_types

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# 5. Streaming
# ---------------------------------------------------------------------------


async def test_frame_index_starts_at_zero() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    await frame_queue.put(make_frame())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    frame_msgs = [m for m in transport.sent if m["msg_type"] == "spectrum_frame"]
    assert frame_msgs[0]["frame_index"] == 0

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_frame_index_increments_per_frame() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    await frame_queue.put(make_frame())
    await frame_queue.put(make_frame())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    frame_msgs = [m for m in transport.sent if m["msg_type"] == "spectrum_frame"]
    assert [m["frame_index"] for m in frame_msgs] == [0, 1]

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_frame_carries_correct_config_version() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(config_version=3))
    await frame_queue.put(make_frame())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    frame_msgs = [m for m in transport.sent if m["msg_type"] == "spectrum_frame"]
    assert frame_msgs[0]["config_version"] == 3

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# 6. Error handling
# ---------------------------------------------------------------------------


async def test_fatal_error_stops_session() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    transport.queue_inbound(make_error(fatal=True))

    await run_session_until_done(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


async def test_nonfatal_error_session_continues_streaming() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    transport.queue_inbound(make_error(fatal=False))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    assert session.state == ConnectionState.STREAMING
    assert not task.done()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_disconnect_message_stops_session() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    transport.queue_inbound(make_disconnect())

    await run_session_until_done(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


async def test_transport_recv_failure_stops_session() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    transport.push_error(ConnectionError("connection lost"))

    await run_session_until_done(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_stops_if_transport_send_fails() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.02)
    assert session.state == ConnectionState.STREAMING

    transport.set_send_error(ConnectionError("send failed"))
    await frame_queue.put(make_frame())

    await asyncio.sleep(0.05)

    assert session.state == ConnectionState.DISCONNECTED

    if not task.done():
        task.cancel()
    try:
        await task
    except (asyncio.CancelledError, SessionError):
        pass


# ---------------------------------------------------------------------------
# 7. Runtime StreamConfigAck validation (invalid ack fails session)
# ---------------------------------------------------------------------------


async def test_session_fails_on_runtime_ack_wrong_session_id() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    # Ack with wrong session_id (e.g. stale or from another connection)
    bad_ack = make_stream_config_ack(session_id="WRONG", config_version=99)
    transport.queue_inbound(bad_ack)

    await run_session_until_done(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_runtime_ack_wrong_stream_id() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    bad_ack = make_stream_config_ack(stream_id="OTHER", config_version=99)
    transport.queue_inbound(bad_ack)

    await run_session_until_done(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_on_runtime_stream_config_ack_with_non_ok_status() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    transport.queue_inbound(make_stream_config_ack(status="error", config_version=99))

    await run_session_until_done(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


# ---------------------------------------------------------------------------
# 8. State reset / reconnect safety
# ---------------------------------------------------------------------------


async def test_session_clears_old_state_after_run_exits() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(config_version=5))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    assert session.config_version == 5
    assert session.session_id == _SESSION_ID

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert session.state == ConnectionState.DISCONNECTED
    assert session.session_id is None
    assert session.config_version is None
    assert session.frame_index == 0


async def test_session_does_not_reuse_old_session_id_on_new_run() -> None:
    """A second run() must not carry over session_id from a previous run."""
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    # First run — reach streaming
    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack())
    task1 = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)
    assert session.session_id == _SESSION_ID

    task1.cancel()
    try:
        await task1
    except asyncio.CancelledError:
        pass

    # State must be fully cleared
    assert session.session_id is None

    # Second run with a different header session_id
    transport.session_id_from_header = "ses_new"
    transport.queue_inbound(make_connect_ack(session_id="ses_new"))

    task2 = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.02)

    # Must use the new session_id, not the old one
    assert session.session_id == "ses_new"

    task2.cancel()
    try:
        await task2
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# 9. Config update
# ---------------------------------------------------------------------------


async def test_config_update_sends_new_stream_config() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(config_version=1))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.02)
    assert session.state == ConnectionState.STREAMING

    transport.queue_inbound(make_stream_config_ack(config_version=2))
    new_rf = RFConfig(center_freq_hz=200_000_000, sample_rate_hz=1_000_000, fft_size=4)
    await session.request_config_update(new_rf)

    assert session.config_version == 2
    assert session.frame_index == 0
    stream_configs = [m for m in transport.sent if m["msg_type"] == "stream_config"]
    assert len(stream_configs) == 2  # initial + update

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_config_update_resets_frame_index_to_zero() -> None:
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack())
    transport.queue_inbound(make_stream_config_ack(config_version=1))
    await frame_queue.put(make_frame())
    await frame_queue.put(make_frame())

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)
    assert session.frame_index == 2

    transport.queue_inbound(make_stream_config_ack(config_version=2))
    new_rf = RFConfig(center_freq_hz=300_000_000, sample_rate_hz=1_000_000, fft_size=4)
    await session.request_config_update(new_rf)

    assert session.frame_index == 0

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# 10. binary_ws encoding
# ---------------------------------------------------------------------------


def make_session_binary_ws(
    transport: FakeTransport | None = None,
    codec: FakeCodec | None = None,
) -> Session:
    """Like make_session() but configured to request binary_ws encoding."""
    if transport is None:
        transport = FakeTransport()
    if codec is None:
        codec = FakeCodec()
    config = AgentConfig(
        identity=AgentIdentity(node_id="node_test"),
        server=ServerConfig(url="wss://test", token="test-token"),
        rf=RFConfig(
            center_freq_hz=100_000_000,
            sample_rate_hz=1_000_000,
            fft_size=4,
        ),
        iq=IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=1_000_000,
            center_freq_hz=100_000_000,
        ),
        wire_encoding=WireEncoding.BINARY_WS,
    )
    return Session(config=config, transport=transport, codec=codec)


async def test_binary_ws_spectrum_frames_sent_as_bytes() -> None:
    """In binary_ws mode, spectrum frames must be bytes, not str or dict."""
    transport = FakeTransport()
    session = make_session_binary_ws(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(wire_encoding="binary_ws"))
    transport.queue_inbound(make_stream_config_ack())
    await frame_queue.put(make_frame(bin_count=4))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    binary_msgs = [m for m in transport.sent if isinstance(m, bytes)]
    assert len(binary_msgs) >= 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_binary_ws_control_messages_are_not_bytes() -> None:
    """Control messages (connect, stream_config) stay non-binary in binary_ws mode."""
    transport = FakeTransport()
    session = make_session_binary_ws(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(wire_encoding="binary_ws"))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.02)

    # FakeCodec returns dicts for control messages — none should be bytes
    assert not any(isinstance(m, bytes) for m in transport.sent)
    connect_msgs = [
        m
        for m in transport.sent
        if isinstance(m, dict) and m.get("msg_type") == "connect"
    ]
    assert len(connect_msgs) == 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_session_fails_when_ack_encoding_mismatches_config() -> None:
    """Config requests json_base64 but server acks binary_ws → SessionError."""
    transport = FakeTransport()
    session = make_session(transport)  # default: json_base64
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(wire_encoding="binary_ws"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_session_fails_when_binary_ws_requested_but_json_acked() -> None:
    """Config requests binary_ws but server acks json_base64 → SessionError."""
    transport = FakeTransport()
    session = make_session_binary_ws(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(wire_encoding="json_base64"))

    with pytest.raises(SessionError):
        await asyncio.wait_for(session.run(frame_queue), timeout=1.0)

    assert session.state == ConnectionState.DISCONNECTED


async def test_json_base64_path_sends_non_bytes_frames() -> None:
    """Existing json_base64 path: spectrum frames must not be bytes."""
    transport = FakeTransport()
    session = make_session(transport)
    frame_queue: asyncio.Queue = asyncio.Queue()

    transport.queue_inbound(make_connect_ack(wire_encoding="json_base64"))
    transport.queue_inbound(make_stream_config_ack())
    await frame_queue.put(make_frame(bin_count=4))

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    # FakeCodec.encode_spectrum_frame returns a dict, never bytes
    frame_msgs = [
        m
        for m in transport.sent
        if isinstance(m, dict) and m.get("msg_type") == "spectrum_frame"
    ]
    assert len(frame_msgs) >= 1
    assert not any(isinstance(m, bytes) for m in transport.sent)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
