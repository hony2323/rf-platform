import asyncio
import pytest

from agent.domain import (
    ConnectionState,
    Endianness,
    Layout,
    SampleFormat,
    SpectrumFrame,
)
from agent.session import Session  # adjust import if needed
from agent.config import AgentConfig, AgentIdentity, ServerConfig
from agent.domain import RFConfig, IQDescriptor


class FakeTransport:
    def __init__(self):
        self.sent_messages = []
        self._inbound = asyncio.Queue()
        self.session_id_from_header = None

    async def connect(self):
        return

    async def send(self, msg):
        self.sent_messages.append(msg)

    async def recv(self):
        return await self._inbound.get()

    async def close(self):
        return

    def queue_inbound(self, msg):
        self._inbound.put_nowait(msg)


class FakeCodec:
    def encode_connect(self, *args, **kwargs):
        return {"msg_type": "connect"}

    def encode_stream_config(self, *args, **kwargs):
        return {"msg_type": "stream_config"}

    def encode_spectrum_frame(self, frame, **kwargs):
        return {
            "msg_type": "spectrum_frame",
            "frame_index": kwargs["frame_index"],
        }

    def decode(self, raw):
        return raw


def make_session(transport, codec):

    config = AgentConfig(
        identity=AgentIdentity(node_id="node_test"),
        server=ServerConfig(url="wss://test", token="test"),
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

    return Session(
        config=config,
        transport=transport,
        codec=codec,
    )


# =========================
# Fixtures / Helpers
# =========================


@pytest.fixture
def fake_transport():
    return FakeTransport()


@pytest.fixture
def fake_codec():
    return FakeCodec()


@pytest.fixture
def frame_queue():
    return asyncio.Queue()


@pytest.fixture
def session(fake_transport, fake_codec):
    return make_session(
        transport=fake_transport,
        codec=fake_codec,
    )


def make_frame():
    return SpectrumFrame(
        payload=b"\x00" * 16,
        timestamp_utc="2026-01-01T00:00:00.000Z",
        bin_count=4,
    )


def make_connect_ack(session_id="ses_1"):
    return {
        "msg_type": "connect_ack",
        "session_id": session_id,
        "status": "ok",
        "wire_encoding": "json_base64",
    }


def make_stream_config_ack(session_id="ses_1", stream_id="default", config_version=1):
    return {
        "msg_type": "stream_config_ack",
        "session_id": session_id,
        "stream_id": stream_id,
        "config_version": config_version,
        "status": "ok",
    }


def make_error(fatal: bool):
    return {
        "msg_type": "error",
        "session_id": "ses_1",
        "code": "INVALID_FRAME",
        "message": "bad",
        "fatal": fatal,
    }


def make_disconnect():
    return {
        "msg_type": "disconnect",
        "session_id": "ses_1",
        "reason": "server_shutdown",
    }


async def run_session_once(session, frame_queue, timeout=0.1):
    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(timeout)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# =========================
# Initial State
# =========================


def test_session_starts_in_disconnected_state(session):
    assert session.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_session_moves_to_connecting_when_run_starts(session, frame_queue):
    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.01)
    assert session.state == ConnectionState.CONNECTING
    task.cancel()


# =========================
# Handshake
# =========================


@pytest.mark.asyncio
async def test_session_sends_connect_first_after_transport_connects(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"

    task = asyncio.create_task(session.run(frame_queue))
    await asyncio.sleep(0.05)

    assert fake_transport.sent_messages[0]["msg_type"] == "connect"

    task.cancel()


@pytest.mark.asyncio
async def test_session_moves_to_connected_on_connect_ack(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))

    await run_session_once(session, frame_queue)

    assert session.state in (ConnectionState.CONNECTED, ConnectionState.CONFIGURED)


@pytest.mark.asyncio
async def test_session_rejects_connect_ack_with_mismatched_session_id(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("WRONG"))

    await run_session_once(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_session_sends_stream_config_after_connect_ack(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))

    await asyncio.sleep(0.01)
    fake_transport.queue_inbound(make_stream_config_ack())

    await run_session_once(session, frame_queue)

    sent_types = [m["msg_type"] for m in fake_transport.sent_messages]
    assert "stream_config" in sent_types


@pytest.mark.asyncio
async def test_session_moves_to_configured_on_stream_config_ack(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))
    fake_transport.queue_inbound(make_stream_config_ack())

    await run_session_once(session, frame_queue)

    assert session.state in (ConnectionState.CONFIGURED, ConnectionState.STREAMING)


# =========================
# Frame Gating + Streaming
# =========================


@pytest.mark.asyncio
async def test_session_blocks_frame_send_until_configured(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"

    await frame_queue.put(make_frame())

    await run_session_once(session, frame_queue)

    sent_types = [m["msg_type"] for m in fake_transport.sent_messages]
    assert "spectrum_frame" not in sent_types


@pytest.mark.asyncio
async def test_session_initial_frame_index_starts_at_zero(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))
    fake_transport.queue_inbound(make_stream_config_ack())

    await frame_queue.put(make_frame())

    await run_session_once(session, frame_queue)

    frame_msgs = [
        m for m in fake_transport.sent_messages if m["msg_type"] == "spectrum_frame"
    ]
    assert frame_msgs[0]["frame_index"] == 0


@pytest.mark.asyncio
async def test_session_increments_frame_index_per_frame(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))
    fake_transport.queue_inbound(make_stream_config_ack())

    await frame_queue.put(make_frame())
    await frame_queue.put(make_frame())

    await run_session_once(session, frame_queue)

    frames = [
        m for m in fake_transport.sent_messages if m["msg_type"] == "spectrum_frame"
    ]
    assert [f["frame_index"] for f in frames] == [0, 1]


# =========================
# Errors / Stop
# =========================


@pytest.mark.asyncio
async def test_session_stops_on_fatal_server_error(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))
    fake_transport.queue_inbound(make_error(True))

    await run_session_once(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_session_handles_nonfatal_server_error_without_closing(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))
    fake_transport.queue_inbound(make_error(False))

    await run_session_once(session, frame_queue)

    assert session.state != ConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_session_stops_on_disconnect_message(
    session, fake_transport, frame_queue
):
    fake_transport.session_id_from_header = "ses_1"
    fake_transport.queue_inbound(make_connect_ack("ses_1"))
    fake_transport.queue_inbound(make_disconnect())

    await run_session_once(session, frame_queue)

    assert session.state == ConnectionState.DISCONNECTED
