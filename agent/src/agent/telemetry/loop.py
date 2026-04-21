"""TelemetryLoop — periodic heartbeat and agent_status emission."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from agent.domain import AgentMetrics, ConnectionState


# ---------------------------------------------------------------------------
# Protocols (injection points — telemetry stays blind to internals)
# ---------------------------------------------------------------------------


class SessionView(Protocol):
    @property
    def state(self) -> ConnectionState: ...

    @property
    def session_id(self) -> str | None: ...


class TelemetrySender(Protocol):
    async def send_heartbeat(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
    ) -> None: ...

    async def send_agent_status(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
        metrics: AgentMetrics,
    ) -> None: ...


class MetricsSource(Protocol):
    """Minimal interface TelemetryLoop needs from a metrics object."""

    def snapshot(self) -> AgentMetrics: ...


# ---------------------------------------------------------------------------
# TelemetryLoop
# ---------------------------------------------------------------------------


class TelemetryLoop:
    """Drives periodic telemetry emission.

    Two independent loops run concurrently:
      * heartbeat loop  — fires every heartbeat_interval_sec
      * status loop     — fires every status_interval_sec

    Both loops only emit when session state is STREAMING and session_id is set.
    Drop counters are reset only after a real agent_status is sent.
    Sender exceptions propagate and stop the loop.
    """

    def __init__(
        self,
        node_id: str,
        session: SessionView,
        sender: TelemetrySender,
        metrics: MetricsSource,
        heartbeat_interval_sec: float,
        status_interval_sec: float,
        clock: Callable[[], str],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        if heartbeat_interval_sec <= 0:
            raise ValueError(
                f"heartbeat_interval_sec must be > 0, got {heartbeat_interval_sec!r}"
            )
        if status_interval_sec <= 0:
            raise ValueError(
                f"status_interval_sec must be > 0, got {status_interval_sec!r}"
            )
        self._node_id = node_id
        self._session = session
        self._sender = sender
        self._metrics = metrics
        self._heartbeat_interval = heartbeat_interval_sec
        self._status_interval = status_interval_sec
        self._clock = clock
        self._sleep = sleep

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run heartbeat and status loops until cancelled."""
        hb_task = asyncio.create_task(self._heartbeat_loop())
        st_task = asyncio.create_task(self._status_loop())
        try:
            await asyncio.gather(hb_task, st_task)
        finally:
            hb_task.cancel()
            st_task.cancel()
            with suppress(asyncio.CancelledError):
                await hb_task
            with suppress(asyncio.CancelledError):
                await st_task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _can_emit(self) -> bool:
        return (
            self._session.state == ConnectionState.STREAMING
            and self._session.session_id is not None
        )

    async def _heartbeat_loop(self) -> None:
        while True:
            await self._sleep(self._heartbeat_interval)
            if self._can_emit():
                session_id = self._session.session_id
                assert session_id is not None  # guaranteed by _can_emit
                await self._sender.send_heartbeat(
                    node_id=self._node_id,
                    session_id=session_id,
                    timestamp_utc=self._clock(),
                )

    async def _status_loop(self) -> None:
        while True:
            await self._sleep(self._status_interval)
            if self._can_emit():
                session_id = self._session.session_id
                assert session_id is not None  # guaranteed by _can_emit
                metrics = self._metrics.snapshot()
                await self._sender.send_agent_status(
                    node_id=self._node_id,
                    session_id=session_id,
                    timestamp_utc=self._clock(),
                    metrics=metrics,
                )
