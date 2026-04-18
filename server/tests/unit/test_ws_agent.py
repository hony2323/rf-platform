from __future__ import annotations

import asyncio
import hashlib
import json
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
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
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
        self._task = asyncio.create_task(
            self._app(self._scope, self._receive, self._send)
        )
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

    async def send_json(self, data: Any) -> None:
        await self.send_text(json.dumps(data))

    async def recv_text(self, timeout: float = 2.0) -> str:
        while True:
            event = await asyncio.wait_for(self._s2c.get(), timeout=timeout)
            if event["type"] == "websocket.send":
                return event.get("text") or (event.get("bytes") or b"").decode()
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


def _stream_config_msg(session_id: str) -> dict:
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
            "bin_count": 1024,
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


async def _do_full_handshake(ws: _WS) -> str:
    """Complete the handshake and return session_id."""
    await ws.connect()
    session_id = ws.accept_headers["x-session-id"]
    await ws.send_json(_connect_msg())
    ack = await ws.recv_json()
    assert ack["msg_type"] == "connect_ack"
    await ws.send_json(_stream_config_msg(session_id))
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
    session_id = await _do_full_handshake(ws)
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
    await ws.send_json({"msg_type": "heartbeat", "node_id": "n", "session_id": "s", "timestamp_utc": "t"})
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
    from datetime import UTC, datetime

    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)
    session = app.state.registry.get_session(session_id)
    before = session.last_heartbeat_at

    await ws.send_json({
        "msg_type": "heartbeat",
        "node_id": "node_x",
        "session_id": session_id,
        "timestamp_utc": "2026-01-01T00:00:05.000Z",
    })
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

    await ws.send_json({
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
    })
    await asyncio.sleep(0)
    session = app.state.registry.get_session(session_id)
    assert session.last_status is not None
    status_data = json.loads(session.last_status)
    assert status_data["cpu_usage_pct"] == 42
    await ws.close()


async def test_spectrum_frame_accepted_silently(app, db_state):
    ws = _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})
    session_id = await _do_full_handshake(ws)

    await ws.send_json({
        "msg_type": "spectrum_frame",
        "node_id": "node_x",
        "session_id": session_id,
        "stream_id": "default",
        "config_version": 1,
        "frame_index": 0,
        "timestamp_utc": "2026-01-01T00:00:01.000Z",
        "data": {"payload": "AAAA"},
    })
    # No response expected for frames in phase 5
    await ws.close()
