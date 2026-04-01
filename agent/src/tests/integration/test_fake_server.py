"""Tests for FakeAgentServer.

Connects with a real WebSocket client over TCP.  These tests verify that
the fake server itself behaves correctly — they do NOT test real agent
components.

All tests are marked integration (require a real network socket).
"""

from __future__ import annotations

import base64
import json
import struct
from typing import Any

import pytest
from websockets.asyncio.client import connect

from fake_server import FakeAgentServer, FakeServerConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONNECT_MSG: dict[str, Any] = {
    "msg_type": "connect",
    "node_id": "test-node",
    "protocol_version": "0.3",
    "agent_version": "0.3.0",
    "requested_encoding": "json_base64",
}

_STREAM_CONFIG_TEMPLATE: dict[str, Any] = {
    "msg_type": "stream_config",
    "node_id": "test-node",
    "stream_id": "default",
    "timestamp_utc": "2026-01-01T00:00:00.000Z",
    "rf": {
        "center_freq_hz": 100_000_000,
        "sample_rate_hz": 2_000_000,
        "fft_size": 1024,
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


def _stream_config(session_id: str) -> dict[str, Any]:
    return {**_STREAM_CONFIG_TEMPLATE, "session_id": session_id}


def _spectrum_frame(session_id: str, frame_index: int, n_bins: int = 4) -> dict[str, Any]:
    payload = base64.b64encode(struct.pack(f"<{n_bins}f", *[-80.0] * n_bins)).decode()
    return {
        "msg_type": "spectrum_frame",
        "node_id": "test-node",
        "session_id": session_id,
        "stream_id": "default",
        "config_version": 1,
        "frame_index": frame_index,
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
        "payload": payload,
        "bin_count": n_bins,
    }


async def _connect_ws(url: str, token: str | None = None) -> Any:
    """Open a WebSocket connection with optional Bearer token."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return await connect(url, additional_headers=headers, compression=None)


async def _do_handshake(ws: Any, session_id: str) -> None:
    """Perform connect → connect_ack → stream_config → stream_config_ack."""
    await ws.send(json.dumps(_CONNECT_MSG))
    ack = json.loads(await ws.recv())
    assert ack["msg_type"] == "connect_ack"

    await ws.send(json.dumps(_stream_config(session_id)))
    sc_ack = json.loads(await ws.recv())
    assert sc_ack["msg_type"] == "stream_config_ack"


# ---------------------------------------------------------------------------
# HTTP upgrade / session_id header
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_fake_server_returns_session_id_header() -> None:
    """Server injects X-Session-Id into the 101 Switching Protocols response."""
    async with FakeAgentServer() as server:
        ws = await _connect_ws(server.ws_url)
        try:
            sid = ws.response.headers.get("X-Session-Id")
            assert sid is not None, "X-Session-Id header missing from upgrade response"
            assert len(sid) > 0
        finally:
            await ws.close()


@pytest.mark.integration
async def test_fake_server_session_id_is_unique_per_connection() -> None:
    """Each connection gets a distinct session_id."""
    async with FakeAgentServer() as server:
        ws1 = await _connect_ws(server.ws_url)
        ws2 = await _connect_ws(server.ws_url)
        try:
            sid1 = ws1.response.headers.get("X-Session-Id", "")
            sid2 = ws2.response.headers.get("X-Session-Id", "")
            assert sid1 != sid2
        finally:
            await ws1.close()
            await ws2.close()


@pytest.mark.integration
async def test_fake_server_rejects_wrong_token() -> None:
    """Server returns HTTP 401 when the bearer token does not match."""
    cfg = FakeServerConfig(expected_token="secret")
    async with FakeAgentServer(cfg) as server:
        with pytest.raises(Exception):
            # websockets raises on non-101 response
            await _connect_ws(server.ws_url, token="wrong-token")


@pytest.mark.integration
async def test_fake_server_accepts_correct_token() -> None:
    """Server accepts a connection when the bearer token matches."""
    cfg = FakeServerConfig(expected_token="correct-token")
    async with FakeAgentServer(cfg) as server:
        ws = await _connect_ws(server.ws_url, token="correct-token")
        try:
            assert ws.response.headers.get("X-Session-Id") is not None
        finally:
            await ws.close()


# ---------------------------------------------------------------------------
# Handshake
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_fake_server_sends_connect_ack_after_valid_connect() -> None:
    """Server replies with connect_ack after a valid connect message."""
    async with FakeAgentServer() as server:
        async with await _connect_ws(server.ws_url) as ws:
            await ws.send(json.dumps(_CONNECT_MSG))
            reply = json.loads(await ws.recv())

    assert reply["msg_type"] == "connect_ack"
    assert reply["wire_encoding"] == "json_base64"
    assert reply["status"] == "ok"
    assert "session_id" in reply


@pytest.mark.integration
async def test_fake_server_sends_stream_config_ack_after_valid_stream_config() -> None:
    """Server replies with stream_config_ack after a valid stream_config."""
    async with FakeAgentServer() as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")

            await ws.send(json.dumps(_CONNECT_MSG))
            json.loads(await ws.recv())  # connect_ack

            await ws.send(json.dumps(_stream_config(sid)))
            sc_ack = json.loads(await ws.recv())

    assert sc_ack["msg_type"] == "stream_config_ack"
    assert sc_ack["status"] == "ok"
    assert sc_ack["stream_id"] == "default"
    assert isinstance(sc_ack["config_version"], int)


@pytest.mark.integration
async def test_fake_server_session_id_in_acks_matches_header() -> None:
    """The session_id in connect_ack and stream_config_ack matches X-Session-Id."""
    async with FakeAgentServer() as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")

            await ws.send(json.dumps(_CONNECT_MSG))
            ack = json.loads(await ws.recv())
            assert ack["session_id"] == sid

            await ws.send(json.dumps(_stream_config(sid)))
            sc_ack = json.loads(await ws.recv())
            assert sc_ack["session_id"] == sid


# ---------------------------------------------------------------------------
# Message recording
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_fake_server_records_spectrum_frame() -> None:
    """Server records spectrum_frame messages in the connection record."""
    async with FakeAgentServer() as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")
            await _do_handshake(ws, sid)
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=0)))
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=1)))

    record = server.connections[0]
    assert len(record.frames) == 2
    assert record.frames[0]["frame_index"] == 0
    assert record.frames[1]["frame_index"] == 1


@pytest.mark.integration
async def test_fake_server_records_heartbeat_and_agent_status() -> None:
    """Server records heartbeat and agent_status messages."""
    heartbeat: dict[str, Any] = {
        "msg_type": "heartbeat",
        "node_id": "test-node",
        "session_id": "",  # filled below
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
    }
    status: dict[str, Any] = {
        "msg_type": "agent_status",
        "node_id": "test-node",
        "session_id": "",
        "timestamp_utc": "2026-01-01T00:00:00.000Z",
        "metrics": {
            "cpu_usage_pct": 5.0,
            "throttled": False,
            "tx_bytes_per_sec": 1024,
            "queue_depth": 0,
            "queue_fill_pct": 0.0,
            "drops": {"local_throttle": 0, "queue_overflow": 0, "server_rejected": 0},
        },
    }

    async with FakeAgentServer() as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")
            await _do_handshake(ws, sid)
            await ws.send(json.dumps({**heartbeat, "session_id": sid}))
            await ws.send(json.dumps({**status, "session_id": sid}))

    record = server.connections[0]
    assert len(record.heartbeats) == 1
    assert record.heartbeats[0]["msg_type"] == "heartbeat"
    assert len(record.statuses) == 1
    assert record.statuses[0]["msg_type"] == "agent_status"


@pytest.mark.integration
async def test_fake_server_connect_msg_is_recorded() -> None:
    """connect and stream_config messages are stored in the connection record."""
    async with FakeAgentServer() as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")
            await _do_handshake(ws, sid)

    record = server.connections[0]
    assert record.connect_msg is not None
    assert record.connect_msg["msg_type"] == "connect"
    assert record.stream_config_msg is not None
    assert record.stream_config_msg["msg_type"] == "stream_config"


# ---------------------------------------------------------------------------
# Error injection
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_fake_server_sends_nonfatal_error_when_configured() -> None:
    """Server sends a non-fatal error after the configured frame count."""
    cfg = FakeServerConfig(send_nonfatal_error_after_n_frames=2)
    async with FakeAgentServer(cfg) as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")
            await _do_handshake(ws, sid)

            # Send 2 frames; the second triggers the nonfatal error
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=0)))
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=1)))

            err = json.loads(await ws.recv())

    assert err["msg_type"] == "error"
    assert err["fatal"] is False
    # 2 frames recorded even though an error was sent
    assert len(server.connections[0].frames) == 2


@pytest.mark.integration
async def test_fake_server_sends_fatal_error_when_configured() -> None:
    """Server sends a fatal error and closes after the configured frame count."""
    cfg = FakeServerConfig(send_fatal_error_after_n_frames=1)
    async with FakeAgentServer(cfg) as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")
            await _do_handshake(ws, sid)
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=0)))
            err = json.loads(await ws.recv())

    assert err["msg_type"] == "error"
    assert err["fatal"] is True
    assert len(server.connections[0].frames) == 1


@pytest.mark.integration
async def test_fake_server_sends_disconnect_when_configured() -> None:
    """Server sends a disconnect message after the configured frame count."""
    cfg = FakeServerConfig(disconnect_after_n_frames=2)
    async with FakeAgentServer(cfg) as server:
        async with await _connect_ws(server.ws_url) as ws:
            sid = ws.response.headers.get("X-Session-Id", "")
            await _do_handshake(ws, sid)
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=0)))
            await ws.send(json.dumps(_spectrum_frame(sid, frame_index=1)))
            disc = json.loads(await ws.recv())

    assert disc["msg_type"] == "disconnect"
    assert len(server.connections[0].frames) == 2


# ---------------------------------------------------------------------------
# Delay injection
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_fake_server_delays_connect_ack_when_configured() -> None:
    """connect_ack is withheld for the configured duration."""
    import time

    cfg = FakeServerConfig(delay_connect_ack_s=0.05)
    async with FakeAgentServer(cfg) as server:
        async with await _connect_ws(server.ws_url) as ws:
            t0 = time.monotonic()
            await ws.send(json.dumps(_CONNECT_MSG))
            json.loads(await ws.recv())  # connect_ack
            elapsed = time.monotonic() - t0

    assert elapsed >= 0.04, f"connect_ack came back too fast: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Multiple connections
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_fake_server_tracks_multiple_connections() -> None:
    """Each connection creates a separate ConnectionRecord."""
    async with FakeAgentServer() as server:
        for _ in range(3):
            ws = await _connect_ws(server.ws_url)
            await ws.close()

    assert len(server.connections) == 3
