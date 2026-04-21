"""Unit tests for TelemetryLoop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from agent.domain import AgentMetrics, ConnectionState
from agent.telemetry.loop import TelemetryLoop
from agent.telemetry.metrics import MetricsCollector

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeSessionView:
    def __init__(
        self,
        state: ConnectionState = ConnectionState.DISCONNECTED,
        session_id: str | None = None,
    ) -> None:
        self.state = state
        self.session_id = session_id


class FakeTelemetrySender:
    def __init__(self) -> None:
        self.heartbeats: list[dict] = []
        self.statuses: list[dict] = []
        self.fail_on_heartbeat: Exception | None = None
        self.fail_on_status: Exception | None = None

    async def send_heartbeat(
        self, node_id: str, session_id: str, timestamp_utc: str
    ) -> None:
        if self.fail_on_heartbeat is not None:
            raise self.fail_on_heartbeat
        self.heartbeats.append(
            {
                "node_id": node_id,
                "session_id": session_id,
                "timestamp_utc": timestamp_utc,
            }
        )

    async def send_agent_status(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
        metrics: AgentMetrics,
    ) -> None:
        if self.fail_on_status is not None:
            raise self.fail_on_status
        self.statuses.append(
            {
                "node_id": node_id,
                "session_id": session_id,
                "timestamp_utc": timestamp_utc,
                "metrics": metrics,
            }
        )


class ControlledSleep:
    """Deterministic sleep: steps are released one at a time per interval bucket.

    Each call to sleep(interval) blocks until release(interval) is called.
    Multiple waiters for the same interval are released in FIFO order.
    """

    def __init__(self) -> None:
        self._queues: dict[float, list[asyncio.Future]] = {}

    async def __call__(self, interval: float) -> None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._queues.setdefault(interval, []).append(fut)
        await fut

    def release(self, interval: float) -> None:
        """Unblock one waiter for the given interval."""
        q = self._queues.get(interval, [])
        if q:
            fut = q.pop(0)
            if not fut.done():
                fut.set_result(None)

    def release_all(self, interval: float) -> None:
        """Unblock all waiters for the given interval."""
        for fut in self._queues.pop(interval, []):
            if not fut.done():
                fut.set_result(None)

    def pending(self, interval: float) -> int:
        return len(self._queues.get(interval, []))


# ---------------------------------------------------------------------------
# Helper context manager
# ---------------------------------------------------------------------------

_HB = 1.0
_ST = 5.0
_NODE = "node_abc"
_SID = "ses_1"
_TS = "2026-01-01T00:00:00+00:00"


def _clock() -> str:
    return _TS


def _make_loop(
    session: FakeSessionView,
    sender: FakeTelemetrySender,
    metrics: MetricsCollector | None = None,
    sleep: ControlledSleep | None = None,
) -> tuple[TelemetryLoop, ControlledSleep]:
    if metrics is None:
        metrics = MetricsCollector()
    if sleep is None:
        sleep = ControlledSleep()
    tl = TelemetryLoop(
        node_id=_NODE,
        session=session,
        sender=sender,
        metrics=metrics,
        heartbeat_interval_sec=_HB,
        status_interval_sec=_ST,
        clock=_clock,
        sleep=sleep,
    )
    return tl, sleep


@asynccontextmanager
async def _running(tl: TelemetryLoop) -> AsyncIterator[asyncio.Task]:
    task = asyncio.create_task(tl.run())
    await asyncio.sleep(0)  # let tl.run() create subtasks
    await asyncio.sleep(0)  # let subtasks start and block on first sleep
    try:
        yield task
    finally:
        task.cancel()
        with pytest.raises((asyncio.CancelledError, Exception)):
            await task


# ---------------------------------------------------------------------------
# Tests — MetricsCollector is already tested; these focus on TelemetryLoop
# ---------------------------------------------------------------------------


async def test_telemetry_emits_heartbeat_on_schedule() -> None:
    session = FakeSessionView(ConnectionState.STREAMING, _SID)
    sender = FakeTelemetrySender()
    tl, sleep = _make_loop(session, sender)

    async with _running(tl):
        sleep.release(_HB)
        await asyncio.sleep(0)
        sleep.release(_HB)
        await asyncio.sleep(0)

    assert len(sender.heartbeats) == 2
    hb = sender.heartbeats[0]
    assert hb["node_id"] == _NODE
    assert hb["session_id"] == _SID
    assert hb["timestamp_utc"] == _TS


async def test_telemetry_emits_agent_status_on_schedule() -> None:
    session = FakeSessionView(ConnectionState.STREAMING, _SID)
    sender = FakeTelemetrySender()
    metrics = MetricsCollector()
    metrics.set_queue_depth(7)
    metrics.set_queue_fill_pct(0.7)
    metrics.set_tx_bytes_per_sec(2048)
    metrics.inc_queue_overflow(4)
    tl, sleep = _make_loop(session, sender, metrics=metrics)

    async with _running(tl):
        sleep.release(_ST)
        await asyncio.sleep(0)

    assert len(sender.statuses) == 1
    st = sender.statuses[0]
    assert st["node_id"] == _NODE
    assert st["session_id"] == _SID
    m: AgentMetrics = st["metrics"]
    assert m.queue_depth == 7
    assert m.queue_fill_pct == 0.7
    assert m.tx_bytes_per_sec == 2048
    assert m.drops.queue_overflow == 4


async def test_telemetry_does_not_emit_when_session_state_disallows_it() -> None:
    for state in (
        ConnectionState.CONNECTING,
        ConnectionState.CONNECTED,
        ConnectionState.CONFIGURED,
        ConnectionState.DISCONNECTED,
    ):
        session = FakeSessionView(state, _SID)
        sender = FakeTelemetrySender()
        tl, sleep = _make_loop(session, sender)

        async with _running(tl):
            sleep.release(_HB)
            await asyncio.sleep(0)
            sleep.release(_ST)
            await asyncio.sleep(0)

        assert sender.heartbeats == [], f"unexpected heartbeat in state {state}"
        assert sender.statuses == [], f"unexpected status in state {state}"


async def test_telemetry_does_not_emit_without_session_id() -> None:
    session = FakeSessionView(ConnectionState.STREAMING, session_id=None)
    sender = FakeTelemetrySender()
    tl, sleep = _make_loop(session, sender)

    async with _running(tl):
        sleep.release(_HB)
        await asyncio.sleep(0)
        sleep.release(_ST)
        await asyncio.sleep(0)

    assert sender.heartbeats == []
    assert sender.statuses == []


async def test_agent_status_snapshot_resets_drop_counters_only_after_send() -> None:
    session = FakeSessionView(ConnectionState.STREAMING, _SID)
    sender = FakeTelemetrySender()
    metrics = MetricsCollector()
    metrics.inc_local_throttle(10)
    tl, sleep = _make_loop(session, sender, metrics=metrics)

    async with _running(tl):
        sleep.release(_ST)
        await asyncio.sleep(0)
        # Second status — no new drops
        sleep.release(_ST)
        await asyncio.sleep(0)

    assert len(sender.statuses) == 2
    assert sender.statuses[0]["metrics"].drops.local_throttle == 10
    assert sender.statuses[1]["metrics"].drops.local_throttle == 0


async def test_telemetry_keeps_drop_counters_when_state_disallows_send() -> None:
    session = FakeSessionView(ConnectionState.CONNECTING, _SID)
    sender = FakeTelemetrySender()
    metrics = MetricsCollector()
    metrics.inc_queue_overflow(7)
    tl, sleep = _make_loop(session, sender, metrics=metrics)

    async with _running(tl):
        # Fire status loop three times while state disallows emission
        sleep.release(_ST)
        await asyncio.sleep(0)
        sleep.release(_ST)
        await asyncio.sleep(0)
        sleep.release(_ST)
        await asyncio.sleep(0)

        # Now allow emission
        session.state = ConnectionState.STREAMING
        sleep.release(_ST)
        await asyncio.sleep(0)

    assert len(sender.statuses) == 1
    assert sender.statuses[0]["metrics"].drops.queue_overflow == 7


async def test_telemetry_cancellation_stops_background_tasks_cleanly() -> None:
    session = FakeSessionView(ConnectionState.STREAMING, _SID)
    sender = FakeTelemetrySender()
    tl, _ = _make_loop(session, sender)

    task = asyncio.create_task(tl.run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # No leaked tasks
    all_tasks = asyncio.all_tasks()
    assert task not in all_tasks


async def test_telemetry_sender_failure_stops_loop() -> None:
    session = FakeSessionView(ConnectionState.STREAMING, _SID)
    sender = FakeTelemetrySender()
    sender.fail_on_heartbeat = RuntimeError("transport broken")
    tl, sleep = _make_loop(session, sender)

    task = asyncio.create_task(tl.run())
    await asyncio.sleep(0)  # let tl.run() create subtasks
    await asyncio.sleep(0)  # let subtasks start and block on first sleep

    sleep.release(_HB)

    with pytest.raises(RuntimeError, match="transport broken"):
        await task


# ---------------------------------------------------------------------------
# Interval validation tests
# ---------------------------------------------------------------------------


def test_telemetry_loop_rejects_zero_heartbeat_interval() -> None:
    session = FakeSessionView()
    sender = FakeTelemetrySender()
    with pytest.raises(ValueError, match="heartbeat_interval_sec"):
        TelemetryLoop(
            node_id=_NODE,
            session=session,
            sender=sender,
            metrics=MetricsCollector(),
            heartbeat_interval_sec=0.0,
            status_interval_sec=_ST,
            clock=_clock,
            sleep=ControlledSleep(),
        )


def test_telemetry_loop_rejects_negative_heartbeat_interval() -> None:
    session = FakeSessionView()
    sender = FakeTelemetrySender()
    with pytest.raises(ValueError, match="heartbeat_interval_sec"):
        TelemetryLoop(
            node_id=_NODE,
            session=session,
            sender=sender,
            metrics=MetricsCollector(),
            heartbeat_interval_sec=-1.0,
            status_interval_sec=_ST,
            clock=_clock,
            sleep=ControlledSleep(),
        )


def test_telemetry_loop_rejects_zero_status_interval() -> None:
    session = FakeSessionView()
    sender = FakeTelemetrySender()
    with pytest.raises(ValueError, match="status_interval_sec"):
        TelemetryLoop(
            node_id=_NODE,
            session=session,
            sender=sender,
            metrics=MetricsCollector(),
            heartbeat_interval_sec=_HB,
            status_interval_sec=0.0,
            clock=_clock,
            sleep=ControlledSleep(),
        )


def test_telemetry_loop_rejects_negative_status_interval() -> None:
    session = FakeSessionView()
    sender = FakeTelemetrySender()
    with pytest.raises(ValueError, match="status_interval_sec"):
        TelemetryLoop(
            node_id=_NODE,
            session=session,
            sender=sender,
            metrics=MetricsCollector(),
            heartbeat_interval_sec=_HB,
            status_interval_sec=-5.0,
            clock=_clock,
            sleep=ControlledSleep(),
        )
