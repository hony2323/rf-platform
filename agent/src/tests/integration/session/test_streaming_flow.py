"""Integration test: streaming flow.

Covers frame transmission after a successful handshake, and the full
parse_iq → FFT → session → codec → transport pipeline.

Timing contract (no sleeps needed):
  Both _send_loop and _recv_loop start together inside asyncio.wait().
  _send_loop drains the pre-populated frame_queue synchronously (no I/O
  yield), then suspends on empty queue.  _recv_loop then processes the
  pre-loaded disconnect and raises.  The frame is always sent before the
  disconnect is consumed — this is deterministic with cooperative asyncio.
"""

from __future__ import annotations

import asyncio
import base64
import json

import numpy as np
import pytest

from agent.domain import SpectrumFrame
from agent.processing.processor import IQProcessor
from agent.session import SessionError
from tests.integration.fixtures.agent_builders import (
    make_agent_config,
    make_iq_chunk,
    make_session,
)
from tests.integration.fixtures.fake_server import FakeServer
from tests.integration.fixtures.message_helpers import (
    connect_ack_msg,
    disconnect_msg,
    stream_config_ack_msg,
)

_CONFIG_VERSION = 1


def _handshake_script(server: FakeServer, stream_id: str = "default") -> None:
    """Pre-load the standard happy-path handshake acks."""
    server.push(connect_ack_msg(server.session_id))
    server.push(stream_config_ack_msg(server.session_id, stream_id, _CONFIG_VERSION))


@pytest.mark.integration
async def test_session_streams_frame_after_successful_handshake() -> None:
    """A pre-queued SpectrumFrame is transmitted after the handshake completes.

    Arrange:
      - Real handshake acks in server script
      - One SpectrumFrame pre-populated in frame_queue
      - Disconnect injected to terminate the session after the frame

    Assert:
      - Fake server receives a spectrum_frame
      - session_id, stream_id, config_version, frame_index == 0 are correct
      - Decoded payload matches the original frame bytes
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-stream-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    _handshake_script(server)
    server.push(disconnect_msg(server.session_id))

    # Pre-populate frame queue with one known frame
    raw_payload = bytes(range(16))  # 16 arbitrary bytes
    known_frame = SpectrumFrame(
        payload=raw_payload,
        timestamp_utc="2026-01-01T00:00:00+00:00",
        bin_count=4,
    )
    await frame_queue.put(known_frame)

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    # -----------------------------------------------------------------------
    # Find the spectrum_frame in what the server received
    # -----------------------------------------------------------------------
    received = [json.loads(m) for m in server.received]
    frame_msgs = [m for m in received if m["msg_type"] == "spectrum_frame"]
    assert len(frame_msgs) == 1, f"Expected 1 spectrum_frame, got {len(frame_msgs)}"

    fm = frame_msgs[0]
    assert fm["session_id"] == server.session_id
    assert fm["stream_id"] == config.stream_id
    assert fm["config_version"] == _CONFIG_VERSION
    assert fm["frame_index"] == 0

    # Payload must decode back to the original bytes
    decoded = base64.b64decode(fm["data"]["payload"])
    assert decoded == raw_payload


@pytest.mark.integration
async def test_frame_index_starts_at_zero_after_handshake() -> None:
    """frame_index must be 0 for the first frame after each handshake."""
    config = make_agent_config()
    server = FakeServer(session_id="ses-idx-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    _handshake_script(server)
    server.push(disconnect_msg(server.session_id))

    frame = SpectrumFrame(
        payload=b"\x00" * 8,
        timestamp_utc="2026-01-01T00:00:00+00:00",
        bin_count=2,
    )
    await frame_queue.put(frame)

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    frame_msgs = [
        json.loads(m)
        for m in server.received
        if json.loads(m)["msg_type"] == "spectrum_frame"
    ]
    assert frame_msgs[0]["frame_index"] == 0


@pytest.mark.integration
async def test_processing_pipeline_to_session_streaming_flow() -> None:
    """Full parse_iq → FFT → session → codec → transport pipeline.

    Arrange:
      - Real IQProcessor (parse_iq + FFTProcessor)
      - Deterministic IQ chunk that yields exactly one SpectrumFrame
      - Real Session + Transport + Codec
      - FakeServer happy path + disconnect

    Assert:
      - Exactly one spectrum_frame reaches the fake server
      - session_id, stream_id, config_version, frame_index are correct
      - Payload length == effective_bin_count * 4 bytes (float32)
      - Frame is not sent before the handshake completes (connect and
        stream_config precede spectrum_frame in the sent message list)
    """
    fft_size = 4
    config = make_agent_config(fft_size=fft_size)
    server = FakeServer(session_id="ses-pipeline-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    _handshake_script(server, stream_id=config.stream_id)
    server.push(disconnect_msg(server.session_id))

    # Run the real processing pipeline synchronously to pre-fill frame_queue
    processor = IQProcessor(config.iq, config.rf)
    chunk = make_iq_chunk(fft_size=fft_size)
    timestamp = "2026-01-01T00:00:00+00:00"
    frames = processor.push(chunk, timestamp)
    assert len(frames) == 1, f"Expected exactly 1 frame from chunk, got {len(frames)}"
    for frame in frames:
        await frame_queue.put(frame)

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    # -----------------------------------------------------------------------
    # Verify spectrum_frame reached the server
    # -----------------------------------------------------------------------
    received = [json.loads(m) for m in server.received]
    msg_types = [m["msg_type"] for m in received]

    assert "spectrum_frame" in msg_types

    connect_idx = msg_types.index("connect")
    sc_idx = msg_types.index("stream_config")
    frame_idx = msg_types.index("spectrum_frame")
    assert connect_idx < sc_idx < frame_idx, (
        "spectrum_frame must be sent after handshake"
    )

    fm = received[frame_idx]
    assert fm["session_id"] == server.session_id
    assert fm["stream_id"] == config.stream_id
    assert fm["config_version"] == _CONFIG_VERSION
    assert fm["frame_index"] == 0

    # Payload length: effective_bin_count float32 values
    payload = base64.b64decode(fm["data"]["payload"])
    assert len(payload) == config.rf.effective_bin_count * 4

    # Sanity: FFT output must not contain NaN or Inf
    values = np.frombuffer(payload, dtype="<f4")
    assert not np.any(np.isnan(values)), "FFT payload contains NaN"
    assert not np.any(np.isinf(values)), "FFT payload contains Inf"


@pytest.mark.integration
async def test_frame_not_sent_before_stream_config_ack() -> None:
    """Frames must not be transmitted until stream_config_ack is received.

    Execution model:
      The session blocks inside _handshake() at `await transport.recv()` waiting
      for stream_config_ack.  _stream() — and therefore _send_loop — does not
      start until _handshake() returns.  One asyncio.sleep(0) yields enough
      for the session to consume the pre-loaded connect_ack, send stream_config,
      and block deterministically on the empty recv queue.

    Assert:
      - After the yield but BEFORE stream_config_ack is pushed:
          server has received connect + stream_config, NO spectrum_frame
      - After stream_config_ack and disconnect are injected:
          exactly one spectrum_frame is received
          ordering: connect < stream_config < spectrum_frame
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-gate-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    # Push only connect_ack — no stream_config_ack yet
    server.push(connect_ack_msg(server.session_id))

    await frame_queue.put(
        SpectrumFrame(
            payload=b"\x00" * 8,
            timestamp_utc="2026-01-01T00:00:00+00:00",
            bin_count=2,
        )
    )

    task = asyncio.create_task(session.run(frame_queue))

    # One yield: session processes connect_ack (no yield) → sends stream_config
    # (no yield) → blocks on empty recv for stream_config_ack (YIELDS here).
    await asyncio.sleep(0)

    # Snapshot: connect + stream_config sent; no spectrum_frame yet
    snapshot = [json.loads(m) for m in server.received]
    snapshot_types = {m["msg_type"] for m in snapshot}
    assert "connect" in snapshot_types
    assert "stream_config" in snapshot_types
    assert "spectrum_frame" not in snapshot_types

    # Unblock: push stream_config_ack then disconnect
    server.push(stream_config_ack_msg(server.session_id, "default", _CONFIG_VERSION))
    server.push(disconnect_msg(server.session_id))

    with pytest.raises(SessionError, match="Server disconnected"):
        await task

    # Full ordering check on completed run
    received = [json.loads(m) for m in server.received]
    msg_types = [m["msg_type"] for m in received]
    assert "spectrum_frame" in msg_types
    connect_idx = msg_types.index("connect")
    sc_idx = msg_types.index("stream_config")
    frame_idx = msg_types.index("spectrum_frame")
    assert connect_idx < sc_idx < frame_idx


@pytest.mark.integration
async def test_streaming_multiple_frames_continuous() -> None:
    """Three pre-queued frames are all transmitted before session ends.

    Timing contract: _send_loop drains the queue without yielding (items are
    available), so all 3 frames are sent before _recv_loop processes the
    pre-loaded disconnect.

    Assert:
      - Exactly 3 spectrum_frame messages received, in order
      - frame_index sequence is [0, 1, 2]
      - All frames share the same session_id and config_version
      - All frames are sent after the handshake messages
    """
    config = make_agent_config()
    server = FakeServer(session_id="ses-multi-001")
    session = make_session(config, server.make_ws_connect())
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    _handshake_script(server)
    server.push(disconnect_msg(server.session_id))

    # Pre-populate with 3 distinct frames
    for i in range(3):
        await frame_queue.put(
            SpectrumFrame(
                payload=bytes([i] * 8),
                timestamp_utc="2026-01-01T00:00:00+00:00",
                bin_count=2,
            )
        )

    with pytest.raises(SessionError, match="Server disconnected"):
        await session.run(frame_queue)

    received = [json.loads(m) for m in server.received]
    frame_msgs = [m for m in received if m["msg_type"] == "spectrum_frame"]

    assert len(frame_msgs) == 3

    for expected_idx, fm in enumerate(frame_msgs):
        assert fm["frame_index"] == expected_idx
        assert fm["session_id"] == server.session_id
        assert fm["config_version"] == _CONFIG_VERSION

    # Handshake messages must precede all spectrum_frames
    msg_types = [m["msg_type"] for m in received]
    last_handshake = max(msg_types.index("connect"), msg_types.index("stream_config"))
    first_frame = next(
        i for i, m in enumerate(received) if m["msg_type"] == "spectrum_frame"
    )
    assert last_handshake < first_frame
