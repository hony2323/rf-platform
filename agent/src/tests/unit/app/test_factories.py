"""Unit tests for make_standard_factories."""

from __future__ import annotations

from agent.app.factories import make_standard_factories
from agent.config import AgentConfig
from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
)
from agent.config import AgentIdentity, ServerConfig
from agent.transport.transport import WebSocketTransport


def _dummy_config() -> AgentConfig:
    iq = IQDescriptor(
        sample_format=SampleFormat.FLOAT32,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=240_000,
        center_freq_hz=433_920_000,
    )
    return AgentConfig(
        identity=AgentIdentity(node_id="test-node"),
        server=ServerConfig(url="ws://localhost:8000/ws/agent", token="tok"),
        rf=RFConfig(center_freq_hz=433_920_000, sample_rate_hz=240_000, fft_size=1024),
        iq=iq,
    )


class TestTransportFactory:
    def test_transport_factory_returns_new_instance_per_call(self) -> None:
        factories = make_standard_factories(lambda cfg: None)  # type: ignore[arg-type]
        cfg = _dummy_config()

        t1 = factories.make_transport(cfg)
        t2 = factories.make_transport(cfg)

        assert isinstance(t1, WebSocketTransport)
        assert isinstance(t2, WebSocketTransport)
        assert t1 is not t2
