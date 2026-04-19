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
from server.app.auth_config import SESSION_COOKIE_NAME, SESSION_SECRET
from server.auth.browser_auth import make_session_cookie
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
            await asyncio.wait_for(self._s2c.get(), timeout=1.0)
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

TOKEN_RAW = "agent_test_token_" + "x" * 46


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
    await db_module.init_db(":memory:")
    factory = async_sessionmaker(db_module._engine, expire_on_commit=False)
    async with factory() as session:
        user = await users_repo.create_user(session, "viewer@test.com", hash_password("pw"))
        agent = await agents_repo.create_agent(session, user.id, "Test Agent", "node_x")
        token_hash = hashlib.sha256(TOKEN_RAW.encode()).hexdigest()
        await create_token(session, agent.id, token_hash)
    return {"user_id": str(user.id), "agent_id": str(agent.id)}


@pytest.fixture
def app(db_state):
    from server.sessions.registry import SessionRegistry
    a = create_app(":memory:")
    a.state.registry = SessionRegistry()
    return a


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_ws(app) -> _WS:
    return _WS(app, "/ws/agent", headers={"authorization": f"Bearer {TOKEN_RAW}"})


def _viewer_ws(app, user_id: str) -> _WS:
    cookie = make_session_cookie(user_id, SESSION_SECRET)
    return _WS(app, "/ws/viewer", headers={"cookie": f"{SESSION_COOKIE_NAME}={cookie}"})


def _connect_msg() -> dict:
    return {
        "msg_type": "connect",
        "protocol_version": "0.3",
        "node_id": "node_x",
        "agent_version": "0.3.0",
        "requested_encoding": "json_base64",
    }


def _stream_config_msg(session_id: str, bin_count: int = 4) -> dict:
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
    return base64.b64encode(struct.pack(f"<{bin_count}f", *[value] * bin_count)).decode()


def _spectrum_frame_msg(session_id: str, config_version: int, frame_index: int, payload: str) -> dict:
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


async def _do_agent_handshake(ws: _WS, bin_count: int = 4) -> str:
    """Connect agent and complete handshake; return session_id."""
    await ws.connect()
    session_id = ws.accept_headers["x-session-id"]
    await ws.send_json(_connect_msg())
    await ws.recv_json()  # connect_ack
    await ws.send_json(_stream_config_msg(session_id, bin_count=bin_count))
    await ws.recv_json()  # stream_config_ack
    return session_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_subscribe_online_agent_succeeds(app, db_state):
    agent = _agent_ws(app)
    await _do_agent_handshake(agent)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    ack = await viewer.recv_json()

    assert ack["msg_type"] == "subscribe_ack"
    assert ack["agent_id"] == db_state["agent_id"]
    assert ack["status"] == "ok"
    assert ack["stream_id"] == "default"

    await viewer.close()
    await agent.close()


async def test_subscribe_sends_ack_then_stream_config(app, db_state):
    agent = _agent_ws(app)
    await _do_agent_handshake(agent)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})

    ack = await viewer.recv_json()
    assert ack["msg_type"] == "subscribe_ack"

    cfg = await viewer.recv_json()
    assert cfg["msg_type"] == "stream_config"
    assert cfg["agent_id"] == db_state["agent_id"]
    assert cfg["config_version"] == 1
    assert "rf" in cfg
    assert "fft_semantics" in cfg
    assert cfg["rf"]["bin_count"] == 4

    await viewer.close()
    await agent.close()


async def test_viewer_receives_fanned_out_frame(app, db_state):
    BIN_COUNT = 4
    agent = _agent_ws(app)
    session_id = await _do_agent_handshake(agent, bin_count=BIN_COUNT)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer.recv_json()  # subscribe_ack
    await viewer.recv_json()  # stream_config

    payload = _make_payload(BIN_COUNT, value=-70.0)
    await agent.send_json(_spectrum_frame_msg(session_id, config_version=1, frame_index=0, payload=payload))

    frame = await viewer.recv_json()
    assert frame["msg_type"] == "spectrum_frame"
    assert frame["agent_id"] == db_state["agent_id"]
    assert frame["frame_index"] == 0
    assert frame["config_version"] == 1
    # Assert on decoded float values, not raw base64
    decoded = struct.unpack(f"<{BIN_COUNT}f", base64.b64decode(frame["data"]["payload"]))
    assert all(pytest.approx(v, abs=1e-4) == -70.0 for v in decoded)

    await viewer.close()
    await agent.close()


async def test_two_viewers_both_receive_frame(app, db_state):
    BIN_COUNT = 4
    agent = _agent_ws(app)
    session_id = await _do_agent_handshake(agent, bin_count=BIN_COUNT)

    viewer1 = _viewer_ws(app, db_state["user_id"])
    await viewer1.connect()
    await viewer1.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer1.recv_json()  # subscribe_ack
    await viewer1.recv_json()  # stream_config

    viewer2 = _viewer_ws(app, db_state["user_id"])
    await viewer2.connect()
    await viewer2.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer2.recv_json()  # subscribe_ack
    await viewer2.recv_json()  # stream_config

    payload = _make_payload(BIN_COUNT)
    await agent.send_json(_spectrum_frame_msg(session_id, config_version=1, frame_index=7, payload=payload))

    frame1 = await viewer1.recv_json()
    frame2 = await viewer2.recv_json()

    assert frame1["msg_type"] == "spectrum_frame"
    assert frame2["msg_type"] == "spectrum_frame"
    assert frame1["frame_index"] == 7
    assert frame2["frame_index"] == 7

    await viewer1.close()
    await viewer2.close()
    await agent.close()


async def test_subscribe_unowned_agent_gets_forbidden_and_closed(app, db_state):
    # Create another user and their agent
    factory = async_sessionmaker(db_module._engine, expire_on_commit=False)
    async with factory() as sess:
        other_user = await users_repo.create_user(sess, "other@test.com", hash_password("pw"))
        other_agent = await agents_repo.create_agent(sess, other_user.id, "Other", "node_other")

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": str(other_agent.id)})

    err = await viewer.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "FORBIDDEN"
    with pytest.raises(WebSocketDisconnect):
        await viewer.recv_json()


async def test_subscribe_offline_agent_gets_agent_offline_and_closed(app, db_state):
    # Agent exists in DB but has no active session
    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})

    err = await viewer.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "AGENT_OFFLINE"
    with pytest.raises(WebSocketDisconnect):
        await viewer.recv_json()


async def test_viewer_receives_stream_config_on_reconfig(app, db_state):
    BIN_COUNT = 4
    agent = _agent_ws(app)
    session_id = await _do_agent_handshake(agent, bin_count=BIN_COUNT)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer.recv_json()  # subscribe_ack
    await viewer.recv_json()  # initial stream_config (config_version=1)

    # Agent reconfigures with a new bin_count
    new_bin_count = 8
    await agent.send_json(_stream_config_msg(session_id, bin_count=new_bin_count))
    await agent.recv_json()  # stream_config_ack

    new_cfg = await viewer.recv_json()
    assert new_cfg["msg_type"] == "stream_config"
    assert new_cfg["config_version"] == 2
    assert new_cfg["rf"]["bin_count"] == new_bin_count
    assert new_cfg["agent_id"] == db_state["agent_id"]

    await viewer.close()
    await agent.close()


async def test_viewer_receives_config_before_new_version_frame(app, db_state):
    BIN_COUNT = 4
    agent = _agent_ws(app)
    session_id = await _do_agent_handshake(agent, bin_count=BIN_COUNT)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer.recv_json()  # subscribe_ack
    await viewer.recv_json()  # initial stream_config

    new_bin_count = 8
    await agent.send_json(_stream_config_msg(session_id, bin_count=new_bin_count))
    await agent.recv_json()  # stream_config_ack
    payload = _make_payload(new_bin_count)
    await agent.send_json(_spectrum_frame_msg(session_id, config_version=2, frame_index=0, payload=payload))

    # Viewer must receive the reconfig stream_config before the new-version frame
    msg1 = await viewer.recv_json()
    msg2 = await viewer.recv_json()
    assert msg1["msg_type"] == "stream_config"
    assert msg1["config_version"] == 2
    assert msg2["msg_type"] == "spectrum_frame"
    assert msg2["config_version"] == 2

    await viewer.close()
    await agent.close()


async def test_viewer_closes_when_agent_disconnects(app, db_state):
    agent = _agent_ws(app)
    await _do_agent_handshake(agent)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer.recv_json()  # subscribe_ack
    await viewer.recv_json()  # stream_config

    await agent.close()

    err = await viewer.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "AGENT_OFFLINE"
    with pytest.raises(WebSocketDisconnect):
        await viewer.recv_json()


async def test_viewer_closes_when_session_replaced(app, db_state):
    # Agent connects first time; viewer subscribes to that session
    agent1 = _agent_ws(app)
    session_id1 = await _do_agent_handshake(agent1)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer.recv_json()  # subscribe_ack
    await viewer.recv_json()  # stream_config
    assert len(app.state.registry.get_viewers_for_session(session_id1)) == 1

    # Agent reconnects — new session evicts viewers of the old session
    agent2 = _agent_ws(app)
    await _do_agent_handshake(agent2)

    err = await viewer.recv_json()
    assert err["msg_type"] == "error"
    assert err["code"] == "AGENT_OFFLINE"
    with pytest.raises(WebSocketDisconnect):
        await viewer.recv_json()

    await agent1.close()
    await agent2.close()


async def test_slow_viewer_full_queue_does_not_block_agent(app, db_state):
    BIN_COUNT = 4
    agent = _agent_ws(app)
    session_id = await _do_agent_handshake(agent, bin_count=BIN_COUNT)

    viewer = _viewer_ws(app, db_state["user_id"])
    await viewer.connect()
    await viewer.send_json({"msg_type": "subscribe", "agent_id": db_state["agent_id"]})
    await viewer.recv_json()  # subscribe_ack
    await viewer.recv_json()  # stream_config

    # Fill the viewer's send_queue to capacity so the next put_nowait would overflow
    agent_session = app.state.registry.get_session_by_agent(db_state["agent_id"])
    subscriptions = app.state.registry.get_viewers_for_session(agent_session.session_id)
    assert len(subscriptions) == 1
    sub = subscriptions[0]
    for _ in range(sub.send_queue.maxsize):
        sub.send_queue.put_nowait("filler")

    # Agent sends a frame — fan-out must drop silently without blocking
    payload = _make_payload(BIN_COUNT)
    await agent.send_json(_spectrum_frame_msg(session_id, config_version=1, frame_index=0, payload=payload))
    await asyncio.sleep(0)

    # Frame was accepted into session.frame_queue; viewer drop was silent
    assert agent_session.frame_queue.qsize() == 1

    await viewer.close()
    await agent.close()
