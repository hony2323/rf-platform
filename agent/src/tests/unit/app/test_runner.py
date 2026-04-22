"""Unit tests for AgentRunner.

All components are replaced with fakes — no real WebSocket, no real SDR,
no real asyncio.sleep.

Fakes share a common pattern:
  * run() records the queues it received, then blocks until cancelled
    (or raises immediately when configured with raises=...).
  * start() / stop() / close() record that they were called.
  * cancelled flag is set when CancelledError is caught inside run().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from agent.app.runner import (
    AgentRunner,
    BuildFailure,
    RunStopReason,
    RunnerFactories,
)
from agent.config import (
    AgentConfig,
    AgentIdentity,
    QueueConfig,
    ReconnectConfig,
    ServerConfig,
)
from agent.domain import (
    ConnectionState,
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    SpectrumFrame,
)
from agent.transport import TransportState

# ---------------------------------------------------------------------------
# Minimal test config
# ---------------------------------------------------------------------------


def _make_config(
    *,
    iq_queue_size: int = 4,
    frame_queue_size: int = 4,
    initial_delay_s: float = 1.0,
    max_delay_s: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = False,
) -> AgentConfig:
    return AgentConfig(
        identity=AgentIdentity(node_id="test-node"),
        server=ServerConfig(url="ws://localhost:8765", token="tok"),
        rf=RFConfig(
            center_freq_hz=100_000_000,
            sample_rate_hz=2_000_000,
            fft_size=1024,
        ),
        iq=IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=2_000_000,
            center_freq_hz=100_000_000,
        ),
        queues=QueueConfig(
            iq_queue_size=iq_queue_size,
            frame_queue_size=frame_queue_size,
        ),
        reconnect=ReconnectConfig(
            initial_delay_s=initial_delay_s,
            max_delay_s=max_delay_s,
            backoff_factor=backoff_factor,
            jitter=jitter,
        ),
    )


# ---------------------------------------------------------------------------
# Fake components
# ---------------------------------------------------------------------------


class FakeTransport:
    def __init__(self) -> None:
        self.closed = False

    @property
    def state(self) -> TransportState:
        return TransportState.CLOSED

    @property
    def session_id_from_header(self) -> str | None:
        return None

    async def connect(self, url: str, token: str) -> None:
        pass

    async def send(self, message: str) -> None:
        pass

    async def recv(self) -> str:
        return ""

    async def close(self) -> None:
        self.closed = True


class FakeCodec:
    """Runner never calls codec directly — it just passes it to factories."""


class FakeSource:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.started = False
        self.stopped = False
        self.cancelled = False
        self.iq_queue: asyncio.Queue[bytes] | None = None
        self._raises = raises

    @property
    def descriptor(self) -> IQDescriptor:
        return IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=2_000_000,
            center_freq_hz=100_000_000,
        )

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        self.iq_queue = output
        if self._raises is not None:
            raise self._raises
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeProcessor:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.cancelled = False
        self.iq_queue: asyncio.Queue[bytes] | None = None
        self.frame_queue: asyncio.Queue[SpectrumFrame] | None = None
        self._raises = raises

    async def run(
        self,
        iq_queue: asyncio.Queue[bytes],
        frame_queue: asyncio.Queue[SpectrumFrame],
    ) -> None:
        self.iq_queue = iq_queue
        self.frame_queue = frame_queue
        if self._raises is not None:
            raise self._raises
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class FakeSession:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.cancelled = False
        self.frame_queue: asyncio.Queue[SpectrumFrame] | None = None
        self._raises = raises

    @property
    def state(self) -> ConnectionState:
        return ConnectionState.DISCONNECTED

    @property
    def session_id(self) -> str | None:
        return None

    async def run(self, frame_queue: asyncio.Queue[SpectrumFrame]) -> None:
        self.frame_queue = frame_queue
        if self._raises is not None:
            raise self._raises
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    async def request_config_update(self, rf_config: Any) -> None:
        pass


class FakeTelemetry:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.running = False
        self.cancelled = False
        self._raises = raises

    async def run(self) -> None:
        self.running = True
        if self._raises is not None:
            raise self._raises
        try:
            await asyncio.sleep(float("inf"))
        except asyncio.CancelledError:
            self.cancelled = True
            raise


# ---------------------------------------------------------------------------
# Factory builder helper
# ---------------------------------------------------------------------------


@dataclass
class _FakeRepo:
    """Tracks all fake instances created during a test run."""

    transports: list[FakeTransport] = field(default_factory=list)
    sources: list[FakeSource] = field(default_factory=list)
    processors: list[FakeProcessor] = field(default_factory=list)
    sessions: list[FakeSession] = field(default_factory=list)
    telemetries: list[FakeTelemetry] = field(default_factory=list)
    sleep_calls: list[float] = field(default_factory=list)


def _make_runner(
    config: AgentConfig | None = None,
    *,
    source_raises: Exception | None = None,
    processor_raises: Exception | None = None,
    session_raises: Exception | None = None,
    telemetry_raises: Exception | None = None,
    # Raise during the factory call itself (build-phase failure)
    transport_factory_raises: Exception | None = None,
    source_factory_raises: Exception | None = None,
) -> tuple[AgentRunner, _FakeRepo]:
    cfg = config or _make_config()
    repo = _FakeRepo()

    async def fake_sleep(delay: float) -> None:
        repo.sleep_calls.append(delay)

    def make_transport(c: AgentConfig) -> FakeTransport:
        if transport_factory_raises is not None:
            raise transport_factory_raises
        t = FakeTransport()
        repo.transports.append(t)
        return t

    def make_codec(c: AgentConfig) -> FakeCodec:
        return FakeCodec()

    def make_source(c: AgentConfig) -> FakeSource:
        if source_factory_raises is not None:
            raise source_factory_raises
        s = FakeSource(raises=source_raises)
        repo.sources.append(s)
        return s

    def make_processor(c: AgentConfig) -> FakeProcessor:
        p = FakeProcessor(raises=processor_raises)
        repo.processors.append(p)
        return p

    def make_session(
        c: AgentConfig, transport: Any, codec: Any, on_connected: Any = None
    ) -> FakeSession:
        s = FakeSession(raises=session_raises)
        repo.sessions.append(s)
        return s

    def make_telemetry(
        c: AgentConfig, session: Any, metrics: Any, transport: Any, codec: Any
    ) -> FakeTelemetry:
        t = FakeTelemetry(raises=telemetry_raises)
        repo.telemetries.append(t)
        return t

    factories = RunnerFactories(
        make_source=make_source,
        make_processor=make_processor,
        make_transport=make_transport,
        make_codec=make_codec,
        make_session=make_session,
        make_telemetry=make_telemetry,
    )
    runner = AgentRunner(config=cfg, factories=factories, sleep=fake_sleep)
    return runner, repo


# ---------------------------------------------------------------------------
# Construction / wiring
# ---------------------------------------------------------------------------


async def test_runner_creates_fresh_queues_and_components_for_each_attempt() -> None:
    """Each run_once() call produces new component instances."""
    # Give session a quick failure so run_once() returns instead of blocking
    runner, repo = _make_runner(session_raises=RuntimeError("boom"))

    await runner.run_once()
    await runner.run_once()

    assert len(repo.sources) == 2
    assert len(repo.processors) == 2
    assert len(repo.sessions) == 2
    assert len(repo.telemetries) == 2
    assert repo.sources[0] is not repo.sources[1]
    assert repo.processors[0] is not repo.processors[1]


async def test_runner_passes_iq_queue_to_source_and_processor() -> None:
    """The same iq_queue is given to both source.run() and processor.run()."""
    runner, repo = _make_runner(session_raises=RuntimeError("done"))

    await runner.run_once()

    source = repo.sources[0]
    processor = repo.processors[0]
    assert source.iq_queue is not None
    assert processor.iq_queue is not None
    assert source.iq_queue is processor.iq_queue


async def test_runner_passes_frame_queue_to_processor_and_session() -> None:
    """The same frame_queue is given to both processor.run() and session.run()."""
    runner, repo = _make_runner(session_raises=RuntimeError("done"))

    await runner.run_once()

    processor = repo.processors[0]
    session = repo.sessions[0]
    assert processor.frame_queue is not None
    assert session.frame_queue is not None
    assert processor.frame_queue is session.frame_queue


# ---------------------------------------------------------------------------
# Happy path / orchestration
# ---------------------------------------------------------------------------


async def test_run_once_starts_source_processor_session_and_telemetry() -> None:
    """All four components are started in a single run_once() call."""
    runner, repo = _make_runner(source_raises=RuntimeError("source exits"))

    await runner.run_once()

    # All created
    assert len(repo.sources) == 1
    assert len(repo.processors) == 1
    assert len(repo.sessions) == 1
    assert len(repo.telemetries) == 1

    # Source was started (start() called before run())
    assert repo.sources[0].started is True

    # Telemetry's run() was invoked
    assert repo.telemetries[0].running is True


async def test_run_once_cancels_all_tasks_on_external_cancel() -> None:
    """External cancellation propagates after all tasks are cancelled."""
    runner, repo = _make_runner()  # All components block forever

    task = asyncio.create_task(runner.run_once())
    await asyncio.sleep(0)  # let tasks start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # All sibling fakes should have been cancelled
    assert repo.sources[0].cancelled is True
    assert repo.processors[0].cancelled is True
    assert repo.sessions[0].cancelled is True
    assert repo.telemetries[0].cancelled is True


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------


async def test_run_once_cancels_siblings_if_session_fails() -> None:
    runner, repo = _make_runner(session_raises=RuntimeError("session blew up"))

    result = await runner.run_once()

    assert result.reason is RunStopReason.COMPONENT_FAILURE
    assert isinstance(result.error, RuntimeError)
    assert repo.sources[0].cancelled is True
    assert repo.processors[0].cancelled is True
    assert repo.telemetries[0].cancelled is True


async def test_run_once_cancels_siblings_if_source_fails() -> None:
    runner, repo = _make_runner(source_raises=RuntimeError("source blew up"))

    result = await runner.run_once()

    assert result.reason is RunStopReason.COMPONENT_FAILURE
    assert isinstance(result.error, RuntimeError)
    assert repo.processors[0].cancelled is True
    assert repo.sessions[0].cancelled is True
    assert repo.telemetries[0].cancelled is True


async def test_run_once_cancels_siblings_if_processor_fails() -> None:
    runner, repo = _make_runner(processor_raises=RuntimeError("processor blew up"))

    result = await runner.run_once()

    assert result.reason is RunStopReason.COMPONENT_FAILURE
    assert isinstance(result.error, RuntimeError)
    assert repo.sources[0].cancelled is True
    assert repo.sessions[0].cancelled is True
    assert repo.telemetries[0].cancelled is True


async def test_run_once_cancels_siblings_if_telemetry_fails() -> None:
    runner, repo = _make_runner(telemetry_raises=RuntimeError("telemetry blew up"))

    result = await runner.run_once()

    assert result.reason is RunStopReason.COMPONENT_FAILURE
    assert isinstance(result.error, RuntimeError)
    assert repo.sources[0].cancelled is True
    assert repo.processors[0].cancelled is True
    assert repo.sessions[0].cancelled is True


# ---------------------------------------------------------------------------
# Restart loop
# ---------------------------------------------------------------------------


async def test_run_forever_retries_after_failed_attempt() -> None:
    """run_forever() calls run_once() again after a component failure."""
    attempt = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal attempt
        attempt += 1
        if attempt >= 2:
            # After the second attempt fails, cancel us
            raise asyncio.CancelledError

    runner, repo = _make_runner(session_raises=RuntimeError("boom"))
    # Replace the runner's sleep to control the loop
    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await runner.run_forever()

    # Two separate run_once() calls means two sets of components
    assert len(repo.sessions) >= 2


async def test_run_forever_uses_fresh_components_on_retry() -> None:
    """Each retry builds a completely new set of components."""
    call_count = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    runner, repo = _make_runner(session_raises=RuntimeError("boom"))
    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await runner.run_forever()

    # Components from second attempt must be distinct objects
    assert repo.sources[0] is not repo.sources[1]
    assert repo.sessions[0] is not repo.sessions[1]
    assert repo.transports[0] is not repo.transports[1]


async def test_run_forever_stops_on_external_cancel() -> None:
    """CancelledError from outside propagates out of run_forever()."""
    runner, repo = _make_runner()  # all block forever

    task = asyncio.create_task(runner.run_forever())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Exactly one attempt was started
    assert len(repo.sources) == 1


# ---------------------------------------------------------------------------
# Fail-fast behavior
# ---------------------------------------------------------------------------


async def test_run_forever_does_not_retry_on_build_failure() -> None:
    """BuildFailure from a factory is re-raised immediately, no retry."""
    runner, repo = _make_runner(
        transport_factory_raises=RuntimeError("bad config — can't build transport")
    )

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(BuildFailure):
        await runner.run_forever()

    # Never slept — no retry was attempted
    assert sleep_calls == []


async def test_run_forever_does_not_retry_on_authentication_error() -> None:
    """AuthenticationError (bad token) stops
    the loop immediately — no point retrying."""
    from agent.transport import AuthenticationError

    runner, repo = _make_runner(
        session_raises=AuthenticationError(
            "Authentication failed: server rejected token (HTTP 401)"
        )
    )

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(AuthenticationError):
        await runner.run_forever()

    # Only one attempt was made, no sleep between retries
    assert len(repo.sessions) == 1
    assert sleep_calls == []


async def test_run_once_raises_build_failure_when_factory_raises() -> None:
    """BuildFailure is raised from run_once() when the source factory fails."""
    runner, repo = _make_runner(source_factory_raises=OSError("hardware not found"))

    with pytest.raises(BuildFailure) as exc_info:
        await runner.run_once()

    assert isinstance(exc_info.value.__cause__, OSError)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


async def test_runner_calls_cleanup_on_components_after_attempt_ends() -> None:
    """source.stop() and transport.close() are called after every attempt."""
    runner, repo = _make_runner(session_raises=RuntimeError("done"))

    await runner.run_once()

    assert repo.sources[0].stopped is True
    assert repo.transports[0].closed is True


async def test_runner_calls_cleanup_even_on_external_cancel() -> None:
    """Cleanup runs even when the runner is externally cancelled."""
    runner, repo = _make_runner()

    task = asyncio.create_task(runner.run_once())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert repo.sources[0].stopped is True
    assert repo.transports[0].closed is True


async def test_runner_calls_cleanup_on_second_attempt_too() -> None:
    """Cleanup is called for every attempt, not just the first."""
    call_count = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError

    runner, repo = _make_runner(session_raises=RuntimeError("boom"))
    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await runner.run_forever()

    for src in repo.sources:
        assert src.stopped is True
    for transport in repo.transports:
        assert transport.closed is True


# ---------------------------------------------------------------------------
# Backoff / sleep injection
# ---------------------------------------------------------------------------


async def test_run_forever_calls_sleep_between_attempts() -> None:
    """run_forever() calls the injected sleep between failed attempts."""
    attempts = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal attempts
        attempts += 1
        if attempts >= 1:
            raise asyncio.CancelledError

    runner, repo = _make_runner(
        config=_make_config(initial_delay_s=2.5, jitter=False),
        session_raises=RuntimeError("boom"),
    )
    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await runner.run_forever()

    # sleep was called with the initial delay
    assert attempts == 1


async def test_run_forever_backoff_grows_across_attempts() -> None:
    """Delay grows by backoff_factor after each failed attempt."""
    sleep_delays: list[float] = []
    attempt = 0

    async def fake_sleep(delay: float) -> None:
        nonlocal attempt
        sleep_delays.append(delay)
        attempt += 1
        if attempt >= 3:
            raise asyncio.CancelledError

    runner, repo = _make_runner(
        config=_make_config(
            initial_delay_s=1.0,
            backoff_factor=2.0,
            max_delay_s=100.0,
            jitter=False,
        ),
        session_raises=RuntimeError("boom"),
    )
    runner._sleep = fake_sleep  # type: ignore[assignment]

    with pytest.raises(asyncio.CancelledError):
        await runner.run_forever()

    # Delays should follow exponential backoff
    assert sleep_delays[0] == pytest.approx(1.0)
    assert sleep_delays[1] == pytest.approx(2.0)
    assert sleep_delays[2] == pytest.approx(4.0)
