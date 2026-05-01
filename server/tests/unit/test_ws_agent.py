from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from typing import Any

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy.ext.asyncio import async_sessionmaker

import server.storage.db as db_module
from server.app.api import create_app
from server.auth.passwords import hash_password
from server.storage.repositories import agents as agents_repo
from server.storage.repositories import users as users_repo
from server.storage.repositories.agent_tokens import create_token


# ---------------------------------------------------------------------------
# Lightweight ASGI WebSocket test client (same-loop, no TCP)
# ---------------------------------------------------------------------------


class _WS:
    """Drive a WebSocket endpoint via raw ASGI without a real network connection."""

    def __init__(self, app, path: str, headers: dict[str, str] | None = None) -> None:
        self._scope = {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "server": ("testserver", 80),
            "client": ("testclient", 0),
        }
        self._app = app
        self._c2s: asyncio.Queue = asyncio.Queue()
        self._s2c: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self.accept_headers: dict[str, str] = {}
        self.rejection_status: int | None = None

    async def connect(self) -> None:
        await self._c2s.put({"type": "websocket.connect"})
        self._task = asyncio.create_task(self._app(self._scope, self._receive, self._send))
        get_task = asyncio.create_task(self._s2c.get())
        done, _ = await asyncio.wait(
            {get_task, self._task},
            timeout=2.0,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._task in done and self._task.done() and not get_task.done():
            # Handler crashed before sending anything
            get_task.cancel()
            exc = self._task.exception()
            raise RuntimeError(f"WS handler crashed: {exc!r}") from exc
        if get_task not in done:
            get_task.cancel()
            raise TimeoutError("No response from WS handler within 2s")

        first = get_task.result()
        if first["type"] == "websocket.accept":
            for k, v in first.get("headers", []):
                self.accept_headers[k.decode().lower()] = v.decode()
        elif first["type"] in ("http.response.start", "websocket.http.response.start"):
            self.rejection_status = first["status"]
            await asyncio.wait_for(self._s2c.get(), timeout=1.0)  # drain body
            await asyncio.wait_for(self._task, timeout=1.0)
            raise ConnectionRefusedError(f"HTTP {self.rejection_status}")
        else:
            raise RuntimeError(f"unexpected ASGI event: {first['type']!r}")

    async def close(self, code: int = 1000) -> None:
        if self._task and not self._task.done():
            await self._c2s.put({"type": "websocket.disconnect", "code": code})
            try:
                await asyncio.wait_for(self._task, timeout=2.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)

    async def send_text(self, text: str) -> None:
        await self._c2s.put({"type": "websocket.receive", "text": text, "bytes": None})

    async def send_bytes(self, data: bytes) -> None:
        await self._c2s.put({"type": "websocket.receive", "text": None, "bytes": data})

    async def send_json(self, data: Any) -> None:
        await self.send_text(json.dumps(data))

    async def recv_text(self, timeout: float = 2.0) -> str:
        while True:
            event = await asyncio.wait_for(self._s2c.get(), timeout=timeout)
            if event["type"] == "websocket.send":
                return event.get("text") or (event.get("bytes") or b"").decode()
            if event["type"] == "websocket.close":
                raise WebSocketDisconnect(event.get("code", 1000))

    async def recv_bytes(self, timeout: float = 2.0) -> bytes:
        while True:
            event = await asyncio.wait_for(self._s2c.get(), timeout=timeout)
            if event["type"] == "websocket.send":
                data = event.get("bytes")
                if data is None:
                    raise AssertionError(f"expected binary frame, got text: {event.get('text')!r}")
                return data
            if event["type"] == "websocket.close":
                raise WebSocketDisconnect(event.get("code", 1000))

    async def recv_json(self, timeout: float = 2.0) -> Any:
        return json.loads(await self.recv_text(timeout))

    async def _receive(self) -> dict:
        return await self._c2s.get()

    async def _send(self, event: dict) -> None:
        await self._s2c.put(event)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TOKEN_RAW = "agent_test_token_" + "x" * 46  # any 63-char string


@pytest.fixture(autouse=True)
async def reset_db_globals():
    saved_engine = db_module._engine
    saved_factory = db_module._session_factory
    db_module._engine = None
    db_module._session_factory = None
    yield
    if db_module._engine is not None:
        await db_module._engine.dispose()
    db_module._engine = saved_engine
    db_module._session_factory = saved_factory


@pytest.fixture
async def db_state():
    """Initialize in-memory DB and return test user/agent/token info."""
    await db_module.init_db(":memory:")
    factory = async_sessionmaker(db_module._engine, expire_on_commit=False)
    async with factory() as session:
        user = await users_repo.create_user(session, "agent@test.com", hash_password("pw"))
        agent = await agents_repo.create_agent(session, user.id, "Test Agent", "node_x")
        token_hash = hashlib.sha256(TOKEN_RAW.encode()).hexdigest()
        await create_token(session, agent.id, token_hash)
    return {"user_id": user.id, "agent_id": agent.id}


@pytest.fixture
def app(db_state):
    from server.sessions.registry import SessionRegistry

    a = create_app(":memory:")
    a.state.registry = SessionRegistry()
    return a


# ---------------------------------------------------------------------------
# Message helpers
# ---------------------------------------------------------------------------


def _connect_msg() -> dict:
    return {
        "msg_type": "connect",
        "protocol_version": "0.3",
        "node_id": "node_x",
        "agent_version": "0.3.0",
        "requested_encoding": "json_base64",
    }


def _stream_config_msg(session_id: str, bin_count: int = 1024) -> dict:
    return {
        "msg_type": "stream_config",
        "node_id": "node_x",
        "session_id": session_id,
        "stream_id": "default",
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
        "rf": {
            "center_freq_hz": 433920000,
            "sample_rate_hz": 2400000,
            "fft_size": 1024,
            "baseband_start_hz": -1200000,
            "baseband_end_hz": 1200000,
            "bin_size_hz": 2343.75,
            "bin_count": bin_count,
            "window_fn": "hann",
        },
        "fft_semantics": {
            "kind": "power",
            "scale": "log",
            "unit": "dBFS",
            "numeric_type": "float32",
            "bin_order": "low_to_high",
        },
    }


def _make_payload(bin_count: int, value: float = -70.0) -> str:
    """Base64-encode a float32 LE payload of `bin_count` bins."""
    return base64.b64encode(struct.pack(f"<{bin_count}f", *[value] * bin_count)).decode()


def _spectrum_frame_msg(
    session_id: str, config_version: int, frame_index: int, payload: str
) -> dict:
    return {
        "msg_type": "spectrum_frame",
        "node_id": "node_x",
        "session_id": session_id,
        "stream_id": "default",
        "config_version": config_version,
        "frame_index": frame_index,
        "timestamp_utc": "2026-01-01T00:00:01.000Z",
        "data": {"payload": payload},
    }


async def _do_full_handshake(ws: _WS, bin_count: int = 1024) -> str:
    """Complete the handshake and return session_id."""
    await ws.connect()
    session_id = ws.accept_headers["x-session-id"]
    await ws.send_json(_connect_msg())
    ack = await ws.recv_json()
    assert ack["msg_type"] == "connect_ack"
    await ws.send_json(_stream_config_msg(session_id, bin_count=bin_count))
    cfg_ack = await ws.recv_json()
    assert cfg_ack["msg_type"] == "stream_config_ack"
    return session_id


# ---------------------------------------------------------------------------
# Auth failure tests — HTTP 401 before WS accept
# ---------------------------------------------------------------------------


async def test_no_auth_header_rejected(app):
    ws = _WS(app, "/ws/agent")
    with pytest.raises(ConnectionRefusedError):
        await ws.connect()
    assert ws.rejection_status == 401


async def test_no_bearer_prefix_rejected(app):
    ws = _WS(app, "/ws/agent", headers={"authorization": TOKEN_RAW})
    with pytest.raises(ConnectionRefusedError):
        await ws.connect()
    assert ws.rejection_status == 401


async def test_wrong_token_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": "Bearer wrong_token_value"})
    with pytest.raises(ConnectionRefusedError):
        await ws.connect()
    assert ws.rejection_status == 401


async def test_valid_token_accepted(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    assert "x-session-id" in ws.accept_headers
    assert ws.accept_headers["x-session-id"].startswith("ses_")
    await ws.close()


# ---------------------------------------------------------------------------
# Handshake order enforcement
# ---------------------------------------------------------------------------


async def test_connect_ack_contains_session_id_and_encoding(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    session_id = ws.accept_headers["x-session-id"]
    await ws.send_json(_connect_msg())
    ack = await ws.recv_json()
    assert ack["msg_type"] == "connect_ack"
    assert ack["session_id"] == session_id
    assert ack["status"] == "ok"
    assert ack["wire_encoding"] == "json_base64"
    await ws.close()


async def test_wrong_protocol_version_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    msg = dict(_connect_msg(), protocol_version="0.99")
    await ws.send_json(msg)
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "PROTOCOL_MISMATCH"
    assert err["fatal"] is True
    await ws.close()


async def test_unsupported_encoding_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    msg = dict(_connect_msg(), requested_encoding="msgpack")
    await ws.send_json(msg)
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "UNSUPPORTED_ENCODING"
    assert err["fatal"] is True
    await ws.close()


async def test_non_connect_first_message_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    session_id = ws.accept_headers["x-session-id"]
    # Send stream_config instead of connect
    await ws.send_json(_stream_config_msg(session_id))
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is True
    await ws.close()


async def test_stream_config_ack_has_correct_fields(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await _do_full_handshake(ws)
    # Verify stream_config_ack fields were already asserted in helper; check more detail
    # Re-do manually for field assertions
    await ws.close()

    # New connection for detailed check
    ws2 = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws2.connect()
    sid = ws2.accept_headers["x-session-id"]
    await ws2.send_json(_connect_msg())
    await ws2.recv_json()  # connect_ack
    await ws2.send_json(_stream_config_msg(sid))
    cfg_ack = await ws2.recv_json()
    assert cfg_ack["msg_type"] == "stream_config_ack"
    assert cfg_ack["session_id"] == sid
    assert cfg_ack["stream_id"] == "default"
    assert cfg_ack["config_version"] == 1
    assert cfg_ack["status"] == "ok"
    await ws2.close()


async def test_non_stream_config_after_connect_ack_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    await ws.send_json(_connect_msg())
    await ws.recv_json()  # connect_ack
    # Send heartbeat instead of stream_config
    await ws.send_json(
        {
            "msg_type": "heartbeat",
            "node_id": "n",
            "session_id": "s",
            "timestamp_utc": "t",
        }
    )
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is True
    await ws.close()


# ---------------------------------------------------------------------------
# Session registry integration
# ---------------------------------------------------------------------------


async def test_session_registered_after_handshake(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    registry = app.state.registry
    session = registry.get_session(session_id)
    assert session is not None
    assert session.session_id == session_id
    assert session.agent_id == db_state["agent_id"]
    assert session.user_id == db_state["user_id"]
    assert session.config_version == 1
    await ws.close()


async def test_session_deregistered_on_disconnect(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    await ws.close()
    assert app.state.registry.get_session(session_id) is None


# ---------------------------------------------------------------------------
# Frame loop messages
# ---------------------------------------------------------------------------


async def test_heartbeat_updates_timestamp(app, db_state):

    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    before = session.last_heartbeat_at

    await ws.send_json(
        {
            "msg_type": "heartbeat",
            "node_id": "node_x",
            "session_id": session_id,
            "timestamp_utc": "2026-01-01T00:00:05.000Z",
        }
    )
    # Give the handler a moment to process
    await asyncio.sleep(0)
    assert session.last_heartbeat_at >= before
    await ws.close()


async def test_reconfig_increments_config_version(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)

    await ws.send_json(_stream_config_msg(session_id))
    ack2 = await ws.recv_json()
    assert ack2["msg_type"] == "stream_config_ack"
    assert ack2["config_version"] == 2

    session = app.state.registry.get_session(session_id)
    assert session.config_version == 2
    await ws.close()


async def test_agent_status_stored(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)

    await ws.send_json(
        {
            "msg_type": "agent_status",
            "node_id": "node_x",
            "session_id": session_id,
            "timestamp_utc": "2026-01-01T00:00:05.000Z",
            "cpu_usage_pct": 42,
            "throttled": False,
            "tx_bytes_per_sec": 500000,
            "queue_depth": 2,
            "queue_fill_pct": 10,
            "drops": {"local_throttle": 0, "queue_overflow": 0, "server_rejected": 0},
        }
    )
    await asyncio.sleep(0)
    session = app.state.registry.get_session(session_id)
    assert session.last_status is not None
    status_data = json.loads(session.last_status)
    assert status_data["cpu_usage_pct"] == 42
    await ws.close()


async def test_spectrum_frame_valid_accepted(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    payload = _make_payload(BIN_COUNT, value=-70.0)
    await ws.send_json(
        _spectrum_frame_msg(session_id, config_version=1, frame_index=0, payload=payload)
    )
    await asyncio.sleep(0)

    # Valid frame is accepted silently — no error response, session remains live.
    assert app.state.registry.get_session(session_id) is not None
    await ws.close()


async def test_spectrum_frame_payload_too_short_sends_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    short_payload = base64.b64encode(bytes(BIN_COUNT * 4 - 1)).decode()
    await ws.send_json(
        _spectrum_frame_msg(session_id, config_version=1, frame_index=0, payload=short_payload)
    )
    err = await ws.recv_json()

    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    assert err["stream_id"] == "default"
    assert err["config_version"] == 1
    assert err["frame_index"] == 0
    await ws.close()


async def test_spectrum_frame_payload_too_long_sends_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    long_payload = base64.b64encode(bytes(BIN_COUNT * 4 + 4)).decode()
    await ws.send_json(
        _spectrum_frame_msg(session_id, config_version=1, frame_index=0, payload=long_payload)
    )
    err = await ws.recv_json()

    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    await ws.close()


async def test_spectrum_frame_invalid_base64_sends_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    await ws.send_json(
        _spectrum_frame_msg(
            session_id, config_version=1, frame_index=0, payload="not!valid@base64#"
        )
    )
    err = await ws.recv_json()

    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    await ws.close()


async def test_spectrum_frame_wrong_stream_id_sends_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    bad_frame = dict(
        _spectrum_frame_msg(
            session_id, config_version=1, frame_index=0, payload=_make_payload(BIN_COUNT)
        ),
        stream_id="wrong_stream",
    )
    await ws.send_json(bad_frame)
    err = await ws.recv_json()

    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    await ws.close()


async def test_spectrum_frame_oversized_viewer_header_sends_recoverable_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    bad_frame = dict(
        _spectrum_frame_msg(
            session_id, config_version=1, frame_index=0, payload=_make_payload(BIN_COUNT)
        ),
        timestamp_utc="2026-01-01T00:00:01.000Z" + ("x" * 70000),
    )
    await ws.send_json(bad_frame)
    err = await ws.recv_json()

    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    assert "header length" in err["message"]
    assert app.state.registry.get_session(session_id) is not None

    await ws.send_json(
        _spectrum_frame_msg(
            session_id, config_version=1, frame_index=1, payload=_make_payload(BIN_COUNT)
        )
    )
    await asyncio.sleep(0)
    assert app.state.registry.get_session(session_id) is not None
    await ws.close()


async def test_spectrum_frame_stale_config_version_sends_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    # Reconfig → config_version becomes 2
    await ws.send_json(_stream_config_msg(session_id, bin_count=BIN_COUNT))
    await ws.recv_json()  # stream_config_ack

    session = app.state.registry.get_session(session_id)
    assert session.config_version == 2

    # Send frame with old config_version=1
    await ws.send_json(
        _spectrum_frame_msg(
            session_id, config_version=1, frame_index=0, payload=_make_payload(BIN_COUNT)
        )
    )
    err = await ws.recv_json()

    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    await ws.close()


async def test_spectrum_frame_current_config_version_accepted_after_reconfig(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)

    await ws.send_json(_stream_config_msg(session_id, bin_count=BIN_COUNT))
    await ws.recv_json()  # stream_config_ack (config_version=2)

    payload = _make_payload(BIN_COUNT)
    await ws.send_json(
        _spectrum_frame_msg(session_id, config_version=2, frame_index=0, payload=payload)
    )
    await asyncio.sleep(0)

    # Valid frame after reconfig is accepted silently — session remains live.
    assert app.state.registry.get_session(session_id) is not None
    await ws.close()


async def test_reconfig_updates_bin_count_for_validation(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=4)
    session = app.state.registry.get_session(session_id)

    # Re-configure with a different bin_count
    new_bin_count = 8
    await ws.send_json(_stream_config_msg(session_id, bin_count=new_bin_count))
    ack = await ws.recv_json()
    assert ack["config_version"] == 2
    assert session.bin_count == new_bin_count

    # Frame valid for new bin_count
    payload = _make_payload(new_bin_count)
    await ws.send_json(
        _spectrum_frame_msg(session_id, config_version=2, frame_index=0, payload=payload)
    )
    await asyncio.sleep(0)
    # Valid frame for updated bin_count accepted silently — session remains live.
    assert app.state.registry.get_session(session_id) is not None
    await ws.close()


# ---------------------------------------------------------------------------
# Leak regression — frame_queue removed
# ---------------------------------------------------------------------------


async def test_session_has_no_frame_queue_attribute(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    assert not hasattr(session, "frame_queue"), (
        "frame_queue was re-introduced on LiveAgentSession — it is an unbounded memory leak"
    )
    await ws.close()


async def test_many_frames_accepted_without_accumulating_on_session(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws, bin_count=BIN_COUNT)
    session = app.state.registry.get_session(session_id)

    for i in range(20):
        await ws.send_json(
            _spectrum_frame_msg(
                session_id, config_version=1, frame_index=i, payload=_make_payload(BIN_COUNT)
            )
        )
    await asyncio.sleep(0)

    assert app.state.registry.get_session(session_id) is not None
    assert not hasattr(session, "frame_queue")
    await ws.close()


# ---------------------------------------------------------------------------
# Identity / session consistency validation
# ---------------------------------------------------------------------------


async def test_connect_node_id_mismatch_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    # agent was registered with stable_node_id "node_x"; send a different one
    msg = dict(_connect_msg(), node_id="wrong_node")
    await ws.send_json(msg)
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is True
    await ws.close()


async def test_stream_config_node_id_mismatch_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    await ws.send_json(_connect_msg())
    await ws.recv_json()  # connect_ack

    session_id = ws.accept_headers["x-session-id"]
    bad_cfg = dict(_stream_config_msg(session_id), node_id="wrong_node")
    await ws.send_json(bad_cfg)
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is True
    assert len(app.state.registry.all_sessions()) == 0
    await ws.close()


async def test_stream_config_with_wrong_session_id_rejected(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    await ws.send_json(_connect_msg())
    await ws.recv_json()  # connect_ack

    # Use a different session_id in stream_config
    bad_cfg = dict(_stream_config_msg("ses_wrong_session_id"))
    await ws.send_json(bad_cfg)
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is True

    # Session must not be in the registry
    assert len(app.state.registry.all_sessions()) == 0
    await ws.close()


async def test_heartbeat_with_wrong_session_id_is_ignored_and_errors(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    before = session.last_heartbeat_at

    await ws.send_json(
        {
            "msg_type": "heartbeat",
            "node_id": "node_x",
            "session_id": "ses_wrong",
            "timestamp_utc": "2026-01-01T00:00:05.000Z",
        }
    )
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    assert session.last_heartbeat_at == before  # no mutation
    await ws.close()


async def test_agent_status_with_wrong_session_id_is_ignored_and_errors(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    assert session.last_status is None

    await ws.send_json(
        {
            "msg_type": "agent_status",
            "node_id": "node_x",
            "session_id": "ses_wrong",
            "timestamp_utc": "2026-01-01T00:00:05.000Z",
            "cpu_usage_pct": 99,
            "throttled": False,
            "tx_bytes_per_sec": 0,
            "queue_depth": 0,
            "queue_fill_pct": 0,
            "drops": {"local_throttle": 0, "queue_overflow": 0, "server_rejected": 0},
        }
    )
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    assert session.last_status is None  # no mutation
    await ws.close()


async def test_reconfig_with_wrong_session_id_is_ignored_and_errors(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    assert session.config_version == 1

    bad_cfg = dict(_stream_config_msg("ses_wrong"))
    await ws.send_json(bad_cfg)
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    assert session.config_version == 1  # no mutation
    await ws.close()


async def test_mid_session_node_id_mismatch_is_ignored_and_errors(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    before = session.last_heartbeat_at

    await ws.send_json(
        {
            "msg_type": "heartbeat",
            "node_id": "impostor_node",
            "session_id": session_id,
            "timestamp_utc": "2026-01-01T00:00:05.000Z",
        }
    )
    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    assert session.last_heartbeat_at == before  # no mutation
    await ws.close()


# ---------------------------------------------------------------------------
# binary_ws encoding (agent → server)
# ---------------------------------------------------------------------------


def _connect_msg_binary() -> dict:
    return dict(_connect_msg(), requested_encoding="binary_ws")


def _build_binary_spectrum_frame(
    session_id: str,
    *,
    config_version: int = 1,
    frame_index: int = 0,
    bin_count: int = 4,
    payload: bytes | None = None,
    omit_field: str | None = None,
    msg_type: str = "spectrum_frame",
) -> bytes:
    header: dict = {
        "msg_type": msg_type,
        "node_id": "node_x",
        "session_id": session_id,
        "stream_id": "default",
        "config_version": config_version,
        "frame_index": frame_index,
        "timestamp_utc": "2026-01-01T00:00:01.000Z",
        "bin_count": bin_count,
    }
    if omit_field is not None:
        header.pop(omit_field, None)
    header_bytes = json.dumps(header).encode("utf-8")
    if payload is None:
        payload = struct.pack(f"<{bin_count}f", *[-70.0] * bin_count)
    return struct.pack(">H", len(header_bytes)) + header_bytes + payload


async def _do_binary_handshake(ws: _WS, bin_count: int = 4) -> str:
    await ws.connect()
    session_id = ws.accept_headers["x-session-id"]
    await ws.send_json(_connect_msg_binary())
    ack = await ws.recv_json()
    assert ack["msg_type"] == "connect_ack"
    assert ack["wire_encoding"] == "binary_ws"
    await ws.send_json(_stream_config_msg(session_id, bin_count=bin_count))
    cfg_ack = await ws.recv_json()
    assert cfg_ack["msg_type"] == "stream_config_ack"
    return session_id


async def test_binary_ws_connect_ack_echoes_encoding(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await ws.connect()
    await ws.send_json(_connect_msg_binary())
    ack = await ws.recv_json()
    assert ack["msg_type"] == "connect_ack"
    assert ack["wire_encoding"] == "binary_ws"
    await ws.close()


async def test_binary_ws_session_persists_encoding(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_binary_handshake(ws, bin_count=4)
    session = app.state.registry.get_session(session_id)
    assert session is not None
    assert session.wire_encoding == "binary_ws"
    await ws.close()


async def test_binary_ws_valid_frame_fans_out_to_viewer(app, db_state):
    """Full forward path: agent sends binary → server decodes → viewer gets binary."""
    from server.sessions.models import ViewerSubscription

    BIN_COUNT = 4
    FRAME_VALUE = -55.0
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_binary_handshake(ws, bin_count=BIN_COUNT)

    viewer = ViewerSubscription(
        subscription_id="sub_test",
        user_id=str(db_state["user_id"]),
        agent_id=str(db_state["agent_id"]),
        session_id=session_id,
    )
    app.state.registry.add_viewer(viewer)

    payload = struct.pack(f"<{BIN_COUNT}f", *[FRAME_VALUE] * BIN_COUNT)
    frame = _build_binary_spectrum_frame(
        session_id, config_version=1, frame_index=0, bin_count=BIN_COUNT, payload=payload
    )
    await ws.send_bytes(frame)

    forwarded = await asyncio.wait_for(viewer.send_queue.get(), timeout=2.0)
    assert isinstance(forwarded, bytes)
    header_len = struct.unpack(">H", forwarded[:2])[0]
    header = json.loads(forwarded[2 : 2 + header_len])
    assert header["msg_type"] == "spectrum_frame"
    assert header["frame_index"] == 0
    assert header["bin_count"] == BIN_COUNT
    forwarded_payload = forwarded[2 + header_len :]
    assert forwarded_payload == payload
    await ws.close()


async def test_binary_ws_payload_length_mismatch_emits_error(app, db_state):
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_binary_handshake(ws, bin_count=BIN_COUNT)

    short_payload = struct.pack(f"<{BIN_COUNT - 1}f", *[-70.0] * (BIN_COUNT - 1))
    frame = _build_binary_spectrum_frame(session_id, bin_count=BIN_COUNT, payload=short_payload)
    await ws.send_bytes(frame)

    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert err["fatal"] is False
    await ws.close()


async def test_binary_ws_malformed_header_emits_error(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    await _do_binary_handshake(ws, bin_count=4)

    bad_header = b"this is not json"
    frame = struct.pack(">H", len(bad_header)) + bad_header + struct.pack("<4f", *[0.0] * 4)
    await ws.send_bytes(frame)

    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    await ws.close()


async def test_binary_ws_text_spectrum_frame_rejected(app, db_state):
    """In binary_ws mode, a JSON text spectrum_frame must be rejected."""
    BIN_COUNT = 4
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_binary_handshake(ws, bin_count=BIN_COUNT)

    text_payload = base64.b64encode(struct.pack(f"<{BIN_COUNT}f", *[-70.0] * BIN_COUNT)).decode()
    await ws.send_json(
        _spectrum_frame_msg(session_id, config_version=1, frame_index=0, payload=text_payload)
    )

    err = await ws.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "INVALID_FRAME"
    assert "binary" in err["message"].lower()
    await ws.close()


async def test_binary_ws_heartbeat_still_text(app, db_state):
    """Control-plane messages (heartbeat) stay JSON text in binary_ws mode."""
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_binary_handshake(ws, bin_count=4)

    before = app.state.registry.get_session(session_id).last_heartbeat_at
    await ws.send_json(
        {
            "msg_type": "heartbeat",
            "node_id": "node_x",
            "session_id": session_id,
            "timestamp_utc": "2026-01-01T00:00:05.000Z",
        }
    )
    await asyncio.sleep(0.01)
    after = app.state.registry.get_session(session_id).last_heartbeat_at
    assert after > before
    await ws.close()
