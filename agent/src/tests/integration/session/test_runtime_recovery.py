"""Integration test: runtime error handling and session recovery.

Covers:
  - Non-fatal server error does not stop the session
  - Fatal server error stops the session immediately
  - Session state is fully reset after a run ends
  - A second session can start cleanly after the first ends
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.domain import ConnectionState, SpectrumFrame
from agent.session import SessionError
from tests.integration.fixtures.agent_builders import make_agent_config, make_session
from tests.integration.fixtures.fake_server import FakeServer
from tests.integration.fixtures.message_helpers import (
    connect_ack_msg,
    disconnect_msg,
    server_error_msg,
    stream_config_ack_msg,
)

_CONFIG_VERSION = 1


def _standard_handshake(server: FakeServer, stream_id: str = "default") -> None:
    server.push(connect_ack_msg(server.session_id))
    server.push(stream_config_ack_msg(server.session_id, stream_id, _CONFIG_VERSION))


@pytest.mark.integration
async def test_nonfatal_error_does_not_stop_streaming() -> None:
    """A non-fatal server error is silently absorbed; streaming continues.

    The session must stop on the subsequent disconnect, not on the error.
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-nonfatal-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    _standard_handshake(server)
    server.push(
        server_error_msg(
            server.session_id,
            code="TEST_WARN",
            message="non-fatal warning",
            fatal=False,
        )
    )
    server.push(disconnect_msg(server.session_id, reason="test_done"))

    # Session must raise on the disconnect, not on the non-fatal error
    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)


@pytest.mark.integration
async def test_fatal_error_stops_streaming() -> None:
    """A fatal server error immediately stops the session.

    The error code must appear in the SessionError message.
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-fatal-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    _standard_handshake(server)
    server.push(
        server_error_msg(
            server.session_id,
            code="AUTH_REVOKED",
            message="token revoked by server",
            fatal=True,
        )
    )

    with pytest.raises(SessionError, match="Fatal server error"):
        await session.run(frame_queue)


@pytest.mark.integration
async def test_session_stops_and_runtime_can_restart_after_disconnect() -> None:
    """State is fully reset after a session ends; a second session starts clean.

    First run:
      - Connect, handshake, receive disconnect
      - After run(): state is DISCONNECTED, session_id is None

    Second run:
      - New Session with a different session_id
      - Completes handshake, receives disconnect
      - session_id reflects the new server's value
    """
    config = make_agent_config()

    # -------------------------------------------------------------------
    # First run
    # -------------------------------------------------------------------
    server1 = FakeServer(session_id="ses-first-run-001")
    session1 = make_session(config, server1.make_ws_connect())
    frame_queue1: asyncio.Queue = asyncio.Queue()

    _standard_handshake(server1)
    server1.push(disconnect_msg(server1.session_id, reason="first run done"))

    with pytest.raises(SessionError, match="Server disconnected"):
        await session1.run(frame_queue1)

    # State must be fully reset after the run
    assert session1.state == ConnectionState.DISCONNECTED
    assert session1.session_id is None
    assert session1.config_version is None

    # -------------------------------------------------------------------
    # Second run — new Session with a distinct session_id
    # -------------------------------------------------------------------
    server2 = FakeServer(session_id="ses-second-run-002")
    assert server2.session_id != server1.session_id

    session2 = make_session(config, server2.make_ws_connect())
    frame_queue2: asyncio.Queue = asyncio.Queue()

    _standard_handshake(server2)
    server2.push(disconnect_msg(server2.session_id, reason="second run done"))

    with pytest.raises(SessionError, match="Server disconnected"):
        await session2.run(frame_queue2)

    assert session2.state == ConnectionState.DISCONNECTED
    assert session2.session_id is None


@pytest.mark.integration
async def test_transport_recv_exception_stops_session() -> None:
    """A transport-level recv() failure raises SessionError and stops the session.

    After the handshake completes, inject a raw RuntimeError into the fake
    server's inbound queue.  WebSocketTransport wraps it as ConnectionError;
    _recv_loop wraps that as SessionError.
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-recv-err-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    _standard_handshake(server)
    server.push_exception(RuntimeError("simulated recv failure"))

    with pytest.raises(SessionError):
        await session.run(frame_queue)


@pytest.mark.integration
async def test_transport_send_exception_stops_session() -> None:
    """A transport-level send() failure raises SessionError and stops the session.

    The handshake (connect + stream_config) succeeds — after_n=2 skips those
    two sends.  The first frame send then raises, which _send_loop wraps as
    SessionError.  _recv_loop (blocked on empty queue) is cancelled cleanly.
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-send-err-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    _standard_handshake(server)
    # Let connect + stream_config pass (2 sends), fail on the frame send
    server.set_send_error(RuntimeError("simulated send failure"), after_n=2)

    await frame_queue.put(
        SpectrumFrame(
            payload=b"\x00" * 8,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            bin_count=2,
        )
    )

    with pytest.raises(SessionError):
        await session.run(frame_queue)


@pytest.mark.integration
async def test_frame_index_resets_after_new_session() -> None:
    """frame_index always starts at 0 in a new session regardless of prior runs.

    First run: sends 2 frames (frame_index 0, 1).
    Second run: sends 1 frame — frame_index must be 0, not 2.
    Session IDs must differ between runs.
    """
    config = make_agent_config()

    # -------------------------------------------------------------------
    # First run — 2 frames
    # -------------------------------------------------------------------
    server1 = FakeServer(session_id="ses-fidx-run1")
    session1 = make_session(config, server1.make_ws_connect())
    fq1: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    server1.push(connect_ack_msg(server1.session_id))
    server1.push(stream_config_ack_msg(server1.session_id, "default", _CONFIG_VERSION))
    server1.push(disconnect_msg(server1.session_id))

    for _ in range(2):
        await fq1.put(
            SpectrumFrame(
                payload=b"\xaa" * 4,
                timestamp_utc="2026-01-01T00:00:00+00:00",
                bin_count=1,
            )
        )

    with pytest.raises(SessionError, match="Server disconnected"):
        await session1.run(fq1)

    first_run_frames = [
        json.loads(m)
        for m in server1.received
        if json.loads(m)["msg_type"] == "spectrum_frame"
    ]
    assert len(first_run_frames) == 2
    assert [fm["frame_index"] for fm in first_run_frames] == [0, 1]

    # -------------------------------------------------------------------
    # Second run — different session_id, frame_index must reset to 0
    # -------------------------------------------------------------------
    server2 = FakeServer(session_id="ses-fidx-run2")
    assert server2.session_id != server1.session_id

    session2 = make_session(config, server2.make_ws_connect())
    fq2: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    server2.push(connect_ack_msg(server2.session_id))
    server2.push(stream_config_ack_msg(server2.session_id, "default", _CONFIG_VERSION))
    server2.push(disconnect_msg(server2.session_id))

    await fq2.put(
        SpectrumFrame(
            payload=b"\xbb" * 4,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            bin_count=1,
        )
    )

    with pytest.raises(SessionError, match="Server disconnected"):
        await session2.run(fq2)

    second_run_frames = [
        json.loads(m)
        for m in server2.received
        if json.loads(m)["msg_type"] == "spectrum_frame"
    ]
    assert len(second_run_frames) == 1
    assert second_run_frames[0]["frame_index"] == 0
    assert second_run_frames[0]["session_id"] == server2.session_id


@pytest.mark.integration
@pytest.mark.parametrize(
    "sid_override, stream_id_override, status_override, match",
    [
        ("WRONG_SESSION", "default", "ok", "session_id mismatch"),
        (None, "WRONG_STREAM", "ok", "stream_id mismatch"),
        (None, "default", "error", "status not ok"),
    ],
    ids=["wrong_session_id", "wrong_stream_id", "bad_status"],
)
async def test_runtime_stream_config_ack_invalid_rejected(
    sid_override: str | None,
    stream_id_override: str,
    status_override: str,
    match: str,
) -> None:
    """An invalid runtime stream_config_ack must stop the session immediately.

    Session policy: strict rejection — any field mismatch or non-ok status
    raises SessionError.  State must NOT be mutated before validation passes.

    Sub-cases (parametrized):
      wrong_session_id  — session_id doesn't match
      wrong_stream_id   — stream_id doesn't match
      bad_status        — status != "ok"
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-bad-ack-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue = asyncio.Queue()

    # Normal handshake first
    _standard_handshake(server)

    # Inject the bad runtime ack (config_version=2 to distinguish from handshake)
    ack_sid = sid_override if sid_override is not None else server.session_id
    server.push(
        stream_config_ack_msg(ack_sid, stream_id_override, 2, status=status_override)
    )

    with pytest.raises(SessionError, match=match):
        await session.run(frame_queue)
