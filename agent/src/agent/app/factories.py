"""Standard production component factories.

Wires WebSocketTransport, JsonBase64Codec, IQProcessor, Session, and
TelemetryLoop into a RunnerFactories bundle ready for AgentRunner.

A new WebSocketTransport is created per reconnect attempt; codec is shared.
Processor, session, and telemetry are constructed fresh per-attempt by the runner.

PipelineTiming and MetricsCollector are shared across attempts so cumulative
stats survive reconnects. The runner's own MetricsCollector arg in
make_telemetry is intentionally ignored in favour of the shared one.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Callable
from typing import Any

from agent.app.runner import RunnerFactories
from agent.config import AgentConfig
from agent.domain import AgentMetrics
from agent.processing import Processor
from agent.processing.processor import IQProcessor
from agent.protocol import JsonBase64Codec, ProtocolCodec
from agent.session import Session
from agent.source.base import IQSource
from agent.telemetry.loop import TelemetryLoop
from agent.telemetry.metrics import MetricsCollector
from agent.telemetry.stage_timing import PipelineTiming
from agent.transport import Transport
from agent.transport.transport import WebSocketTransport


class _TransportSender:
    """Adapts (WebSocketTransport, JsonBase64Codec)
    to TelemetryLoop's TelemetrySender."""

    def __init__(self, transport: Transport, codec: ProtocolCodec) -> None:
        self._transport = transport
        self._codec = codec

    async def send_heartbeat(
        self, node_id: str, session_id: str, timestamp_utc: str
    ) -> None:
        await self._transport.send(
            self._codec.encode_heartbeat(node_id, session_id, timestamp_utc)
        )

    async def send_agent_status(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
        metrics: AgentMetrics,
    ) -> None:
        await self._transport.send(
            self._codec.encode_agent_status(node_id, session_id, timestamp_utc, metrics)
        )


def make_standard_factories(
    source_factory: Callable[[AgentConfig], IQSource],
) -> RunnerFactories:
    """Return a RunnerFactories bundle wired to standard production components."""
    pipeline_timing = PipelineTiming()
    shared_metrics = MetricsCollector(timings=pipeline_timing)

    codec = JsonBase64Codec()

    def make_transport(cfg: AgentConfig) -> Transport:
        return WebSocketTransport()

    def make_codec(cfg: AgentConfig) -> ProtocolCodec:
        return codec

    def make_processor(cfg: AgentConfig) -> Processor:
        return IQProcessor(
            descriptor=cfg.iq,
            rf_config=cfg.rf,
            timings=pipeline_timing,
            metrics=shared_metrics,
        )

    def make_session(
        cfg: AgentConfig,
        t: Transport,
        c: ProtocolCodec,
        source: IQSource,
        processor: Processor,
        on_connected: Callable[[], None] | None = None,
    ) -> Session:
        return Session(
            config=cfg,
            transport=t,
            codec=c,
            source=source,
            processor=processor,
            timings=pipeline_timing,
            metrics=shared_metrics,
            on_connected=on_connected,
        )

    def make_telemetry(
        cfg: AgentConfig,
        session: Any,
        _metrics: MetricsCollector,
        t: Transport,
        c: ProtocolCodec,
    ) -> TelemetryLoop:
        return TelemetryLoop(
            node_id=cfg.identity.node_id,
            session=session,
            sender=_TransportSender(t, c),
            metrics=shared_metrics,
            heartbeat_interval_sec=cfg.telemetry.heartbeat_interval_s,
            status_interval_sec=cfg.telemetry.status_interval_s,
            clock=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat(),
            sleep=asyncio.sleep,
        )

    return RunnerFactories(
        make_source=source_factory,
        make_processor=make_processor,
        make_transport=make_transport,
        make_codec=make_codec,
        make_session=make_session,
        make_telemetry=make_telemetry,
    )
