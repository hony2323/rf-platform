"""Unit tests for Session's inbound `config_request` handling (v0.5)."""

from __future__ import annotations

import asyncio

from agent.config import AgentConfig, AgentIdentity, ServerConfig
from agent.domain import (
    ConnectionState,
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    SpectrumFrame,
    TunerConfig,
    WireEncoding,
)
from agent.protocol import (
    ConfigRequest,
    ConnectAck,
    StreamConfigAck,
)
from agent.session import Session
from agent.transport import TransportState

_SESSION_ID = "ses_test"
_STREAM_ID = "default"


class FakeTransport:
    def __init__(self) -> None:
        self._state = TransportState.CLOSED
        self.session_id_from_header: str | None = _SESSION_ID
        self._inbound: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[object] = []

    @property
    def state(self) -> TransportState:
        return self._state

    async def connect(self, url: str, token: str) -> None:
        self._state = TransportState.OPEN

    async def send(self, msg: object) -> None:
        self.sent.append(msg)

    async def recv(self) -> object:
        item = await self._inbound.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self._state = TransportState.CLOSED

    def queue_inbound(self, msg: object) -> None:
        self._inbound.put_nowait(msg)


class FakeCodec:
    """Captures encode_stream_config kwargs (including request_id) into
    self.last_stream_config_kwargs so tests can assert on what the session sent.
    """

    def __init__(self) -> None:
        self.last_stream_config_kwargs: dict | None = None
        self.last_config_rejected_kwargs: dict | None = None

    def encode_connect(self, **kwargs: object) -> dict:
        return {"msg_type": "connect", **kwargs}

    def encode_stream_config(self, **kwargs: object) -> dict:
        self.last_stream_config_kwargs = dict(kwargs)
        return {"msg_type": "stream_config", **kwargs}

    def encode_spectrum_frame(self, **kwargs: object) -> dict:
        return {"msg_type": "spectrum_frame", **kwargs}

    def encode_heartbeat(self, **kwargs: object) -> dict:
        return {"msg_type": "heartbeat", **kwargs}

    def encode_agent_status(self, **kwargs: object) -> dict:
        return {"msg_type": "agent_status", **kwargs}

    def encode_config_rejected(self, **kwargs: object) -> dict:
        self.last_config_rejected_kwargs = dict(kwargs)
        return {"msg_type": "config_rejected", **kwargs}

    def decode(self, raw: object) -> object:
        # Test pushes typed objects directly.
        return raw


class FakeRetunableSource:
    def __init__(self) -> None:
        self.applied: list[tuple[RFConfig, TunerConfig | None]] = []

    @property
    def descriptor(self) -> IQDescriptor:
        return IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=1_000_000,
            center_freq_hz=100_000_000,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        await asyncio.sleep(3600)

    async def apply_rf_update(self, rf: RFConfig, tuner: TunerConfig | None) -> None:
        self.applied.append((rf, tuner))


class FakeRetuneFailingSource(FakeRetunableSource):
    async def apply_rf_update(self, rf: RFConfig, tuner: TunerConfig | None) -> None:
        raise RuntimeError("hardware fell off")


class FakeNonRetunableSource:
    @property
    def descriptor(self) -> IQDescriptor:
        return IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=1_000_000,
            center_freq_hz=100_000_000,
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        await asyncio.sleep(3600)


class FakeProcessor:
    def __init__(self) -> None:
        self.configured: list[RFConfig] = []

    def configure(self, rf: RFConfig) -> None:
        self.configured.append(rf)

    def push(self, chunk: bytes, timestamp_utc: str) -> list[SpectrumFrame]:
        return []

    async def run(
        self,
        iq_queue: asyncio.Queue[bytes],
        frame_queue: asyncio.Queue[SpectrumFrame],
    ) -> None:
        await asyncio.sleep(3600)


def _make_config() -> AgentConfig:
    return AgentConfig(
        identity=AgentIdentity(node_id="node_test"),
        server=ServerConfig(url="wss://test", token="t"),
        rf=RFConfig(
            center_freq_hz=100_000_000,
            sample_rate_hz=1_000_000,
            fft_size=1024,
        ),
        iq=IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=1_000_000,
            center_freq_hz=100_000_000,
        ),
        wire_encoding=WireEncoding.JSON_BASE64,
    )


async def _drive_to_streaming(
    transport: FakeTransport, session: Session
) -> asyncio.Task[None]:
    """Push handshake acks and start the session task; returns once STREAMING."""
    transport.queue_inbound(
        ConnectAck(
            session_id=_SESSION_ID, status="ok", wire_encoding=WireEncoding.JSON_BASE64
        )
    )
    transport.queue_inbound(
        StreamConfigAck(
            session_id=_SESSION_ID, stream_id=_STREAM_ID, config_version=1, status="ok"
        )
    )
    frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue(maxsize=4)
    task = asyncio.create_task(session.run(frame_queue))
    for _ in range(50):
        if session.state == ConnectionState.STREAMING:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError(f"session did not reach STREAMING (state={session.state!r})")


async def test_inbound_config_request_applies_and_resends_stream_config() -> None:
    transport = FakeTransport()
    codec = FakeCodec()
    source = FakeRetunableSource()
    processor = FakeProcessor()
    session = Session(
        config=_make_config(),
        transport=transport,
        codec=codec,
        source=source,
        processor=processor,
    )
    run_task = await _drive_to_streaming(transport, session)
    try:
        new_rf = RFConfig(
            center_freq_hz=433_920_000, sample_rate_hz=2_400_000, fft_size=4096
        )
        transport.queue_inbound(
            ConfigRequest(
                session_id=_SESSION_ID,
                stream_id=_STREAM_ID,
                request_id="req_xyz",
                rf=new_rf,
                tuner=TunerConfig(gain_db=30.5, agc=False),
            )
        )
        # Wait for the spawned handler to send a stream_config carrying the
        # request_id (the handshake's own stream_config was sent earlier
        # without one).
        for _ in range(100):
            kw = codec.last_stream_config_kwargs
            if kw is not None and kw.get("request_id") == "req_xyz":
                break
            await asyncio.sleep(0.01)

        assert source.applied == [(new_rf, TunerConfig(gain_db=30.5, agc=False))]
        assert processor.configured == [new_rf]
        sent = codec.last_stream_config_kwargs
        assert sent is not None
        assert sent["request_id"] == "req_xyz"
        assert sent["rf_config"] is new_rf
    finally:
        run_task.cancel()
        try:
            await run_task
        except BaseException:
            pass


async def test_inbound_config_request_rejects_non_retunable_source() -> None:
    transport = FakeTransport()
    codec = FakeCodec()
    source = FakeNonRetunableSource()
    processor = FakeProcessor()
    session = Session(
        config=_make_config(),
        transport=transport,
        codec=codec,
        source=source,
        processor=processor,
    )
    run_task = await _drive_to_streaming(transport, session)
    try:
        transport.queue_inbound(
            ConfigRequest(
                session_id=_SESSION_ID,
                stream_id=_STREAM_ID,
                request_id="req_no",
                rf=RFConfig(
                    center_freq_hz=100_000_000, sample_rate_hz=1_000_000, fft_size=512
                ),
            )
        )
        for _ in range(100):
            kw = codec.last_config_rejected_kwargs
            if kw is not None and kw.get("request_id") == "req_no":
                break
            await asyncio.sleep(0.01)
        rejected = codec.last_config_rejected_kwargs
        assert rejected is not None
        assert rejected["request_id"] == "req_no"
        assert rejected["code"] == "CONFIG_REJECTED"
        assert "live retune" in rejected["message"]
        assert processor.configured == []
    finally:
        run_task.cancel()
        try:
            await run_task
        except BaseException:
            pass


async def test_inbound_config_request_rejects_when_apply_raises() -> None:
    transport = FakeTransport()
    codec = FakeCodec()
    source = FakeRetuneFailingSource()
    processor = FakeProcessor()
    session = Session(
        config=_make_config(),
        transport=transport,
        codec=codec,
        source=source,
        processor=processor,
    )
    run_task = await _drive_to_streaming(transport, session)
    try:
        transport.queue_inbound(
            ConfigRequest(
                session_id=_SESSION_ID,
                stream_id=_STREAM_ID,
                request_id="req_boom",
                rf=RFConfig(
                    center_freq_hz=100_000_000, sample_rate_hz=1_000_000, fft_size=512
                ),
            )
        )
        for _ in range(100):
            kw = codec.last_config_rejected_kwargs
            if kw is not None and kw.get("request_id") == "req_boom":
                break
            await asyncio.sleep(0.01)
        rejected = codec.last_config_rejected_kwargs
        assert rejected is not None
        assert rejected["request_id"] == "req_boom"
        assert rejected["code"] == "CONFIG_REJECTED"
        assert "hardware fell off" in rejected["message"]
        # Processor must not have been reconfigured when source apply failed.
        assert processor.configured == []
    finally:
        run_task.cancel()
        try:
            await run_task
        except BaseException:
            pass


async def test_inbound_config_request_rejects_session_id_mismatch() -> None:
    transport = FakeTransport()
    codec = FakeCodec()
    source = FakeRetunableSource()
    processor = FakeProcessor()
    session = Session(
        config=_make_config(),
        transport=transport,
        codec=codec,
        source=source,
        processor=processor,
    )
    run_task = await _drive_to_streaming(transport, session)
    try:
        transport.queue_inbound(
            ConfigRequest(
                session_id="not_my_session",
                stream_id=_STREAM_ID,
                request_id="req_wrong",
                rf=RFConfig(
                    center_freq_hz=100_000_000, sample_rate_hz=1_000_000, fft_size=512
                ),
            )
        )
        for _ in range(100):
            kw = codec.last_config_rejected_kwargs
            if kw is not None and kw.get("request_id") == "req_wrong":
                break
            await asyncio.sleep(0.01)
        rejected = codec.last_config_rejected_kwargs
        assert rejected is not None
        assert rejected["code"] == "INVALID_FRAME"
        assert "session_id mismatch" in rejected["message"]
        assert source.applied == []
    finally:
        run_task.cancel()
        try:
            await run_task
        except BaseException:
            pass
