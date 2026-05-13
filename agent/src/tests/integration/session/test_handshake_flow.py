"""Integration test: handshake flow.

Uses real Session, real WebSocketTransport, real JsonBase64Codec.
Fakes only the server-side WebSocket endpoint via FakeServer.

Covered:
  - agent sends connect first, then stream_config
  - stream_config carries correct session_id, stream_id, RF config, FFT semantics
  - handshake state machine drives the real codec and transport end-to-end
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.session import SessionError
from tests.integration.fixtures.agent_builders import make_agent_config, make_session
from tests.integration.fixtures.fake_server import FakeServer
from tests.integration.fixtures.message_helpers import (
    connect_ack_msg,
    disconnect_msg,
    stream_config_ack_msg,
)


@pytest.mark.integration
async def test_session_handshake_happy_path_with_fake_server() -> None:
    """Full happy-path handshake through real Session + Transport + Codec.

    Arrange:
      - FakeServer exposes session_id via X-Session-Id header
      - FakeServer sends connect_ack then stream_config_ack
      - FakeServer sends disconnect to terminate the session cleanly

    Assert:
      - connect is sent first
      - stream_config is sent second
      - stream_config fields match agent config: session_id, stream_id, RF, FFT
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-handshake-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    # Server script: acks + disconnect to stop the session
    server.push(connect_ack_msg(server.session_id))
    server.push(stream_config_ack_msg(server.session_id, "default", config_version=1))
    server.push(disconnect_msg(server.session_id, reason="test_done"))

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    sent = server.received
    assert len(sent) >= 2, f"Expected at least 2 messages, got {len(sent)}"

    # -----------------------------------------------------------------------
    # First message: connect
    # -----------------------------------------------------------------------
    connect = json.loads(sent[0])
    assert connect["msg_type"] == "connect"
    assert connect["node_id"] == config.identity.node_id
    assert connect["protocol_version"] == "0.5"
    assert connect["agent_version"] == config.identity.agent_version
    assert connect["requested_encoding"] == "json_base64"

    # -----------------------------------------------------------------------
    # Second message: stream_config
    # -----------------------------------------------------------------------
    sc = json.loads(sent[1])
    assert sc["msg_type"] == "stream_config"
    assert sc["session_id"] == server.session_id
    assert sc["stream_id"] == config.stream_id
    assert sc["node_id"] == config.identity.node_id

    # RF section
    rf = sc["rf"]
    assert rf["center_freq_hz"] == config.rf.center_freq_hz
    assert rf["sample_rate_hz"] == config.rf.sample_rate_hz
    assert rf["fft_size"] == config.rf.fft_size
    assert rf["bin_count"] == config.rf.effective_bin_count
    assert rf["window_fn"] == config.rf.window_fn.value

    # FFT semantics section
    fft_s = sc["fft_semantics"]
    assert fft_s["kind"] == "power"
    assert fft_s["scale"] == "log"
    assert fft_s["unit"] == "dBFS"
    assert fft_s["numeric_type"] == "float32"
    assert fft_s["bin_order"] == "low_to_high"


@pytest.mark.integration
async def test_handshake_order_is_enforced() -> None:
    """connect must precede stream_config in the sent messages.

    This is a protocol invariant: the agent must not send stream_config
    before the server has acknowledged the connect.
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-order-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    server.push(connect_ack_msg(server.session_id))
    server.push(stream_config_ack_msg(server.session_id, "default", config_version=1))
    server.push(disconnect_msg(server.session_id))

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    msg_types = [json.loads(m)["msg_type"] for m in server.received]
    assert "connect" in msg_types
    assert "stream_config" in msg_types
    connect_idx = msg_types.index("connect")
    sc_idx = msg_types.index("stream_config")
    assert connect_idx < sc_idx


@pytest.mark.integration
async def test_handshake_fails_on_encoding_mismatch() -> None:
    """Session must fail when the server offers an unsupported wire encoding.

    The agent requests json_base64.  If the server responds with a different
    encoding (e.g. "msgpack"), the real codec cannot parse the ConnectAck
    wire_encoding field and raises ValueError, which the session wraps as
    SessionError.
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-enc-mismatch-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    # connect_ack with an unsupported encoding
    server.push(connect_ack_msg(server.session_id, wire_encoding="msgpack"))

    with pytest.raises(SessionError, match="connect_ack"):
        await session.run(frame_queue)


@pytest.mark.integration
async def test_handshake_uses_session_id_from_server_header() -> None:
    """The session_id in stream_config must come from the server's HTTP header.

    The server controls identity; the agent must echo back the exact value
    it received in the X-Session-Id header.
    """
    expected_session_id = "ses-header-xyz-999"
    config = make_agent_config()
    server = FakeServer(session_id=expected_session_id)
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    server.push(connect_ack_msg(expected_session_id))
    server.push(stream_config_ack_msg(expected_session_id, "default", config_version=1))
    server.push(disconnect_msg(expected_session_id))

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    sc = json.loads(server.received[1])
    assert sc["msg_type"] == "stream_config"
    assert sc["session_id"] == expected_session_id
