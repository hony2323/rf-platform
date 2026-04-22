"""AgentRunner — top-level orchestrator.

Wires components together, manages task lifecycle, and drives the
reconnect loop. Owns queues, task startup, sibling cancellation, and
cleanup. Delegates everything else to the component it composes.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from agent.config import AgentConfig
from agent.domain import SpectrumFrame
from agent.processing import Processor
from agent.protocol import ProtocolCodec
from agent.source.base import IQSource
from agent.telemetry import MetricsCollector
from agent.session import FatalSessionError
from agent.transport import AuthenticationError, Transport


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class RunStopReason(Enum):
    NORMAL_EXIT = "normal_exit"  # A component returned without error
    COMPONENT_FAILURE = "component_failure"  # A component raised


@dataclass
class RunResult:
    reason: RunStopReason
    error: BaseException | None = None
    connected: bool = False


class BuildFailure(Exception):
    """Component construction failed. run_forever() does not retry this."""


# ---------------------------------------------------------------------------
# Telemetry runnable protocol
#
# The runner only needs run() from telemetry. Defining a minimal protocol here
# avoids coupling the runner to TelemetryLoop internals and lets tests inject
# simple fakes.
# ---------------------------------------------------------------------------


class _TelemetryRunnable(Protocol):
    async def run(self) -> None: ...


# ---------------------------------------------------------------------------
# Factory types
# ---------------------------------------------------------------------------

SourceFactory = Callable[[AgentConfig], IQSource]
ProcessorFactory = Callable[[AgentConfig], Processor]
TransportFactory = Callable[[AgentConfig], Transport]
CodecFactory = Callable[[AgentConfig], ProtocolCodec]
SessionFactory = Callable[
    [AgentConfig, Transport, ProtocolCodec, "Callable[[], None] | None"], Any
]
TelemetryFactory = Callable[
    [AgentConfig, Any, MetricsCollector, Transport, ProtocolCodec],
    _TelemetryRunnable,
]


@dataclass
class RunnerFactories:
    """All component factories.  Replace any factory with a fake for testing."""

    make_source: SourceFactory
    make_processor: ProcessorFactory
    make_transport: TransportFactory
    make_codec: CodecFactory
    make_session: SessionFactory
    make_telemetry: TelemetryFactory


# ---------------------------------------------------------------------------
# AgentRunner
# ---------------------------------------------------------------------------


class AgentRunner:
    """Top-level agent orchestrator.

    Public API::

        result = await runner.run_once()   # single attempt
        await runner.run_forever()         # retries with backoff

    Ownership:
        - Creates queues, components, and tasks on every attempt.
        - Cancels siblings when any task exits.
        - Calls source.stop() and transport.close() in every path.
        - Applies exponential backoff between retries in run_forever().

    Does NOT own:
        - Protocol message logic
        - Handshake details
        - IQ parsing / FFT
        - Telemetry message schema
        - Transport wire details
    """

    def __init__(
        self,
        config: AgentConfig,
        factories: RunnerFactories,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._factories = factories
        self._sleep: Callable[[float], Awaitable[None]] = (
            sleep if sleep is not None else asyncio.sleep
        )

    # ------------------------------------------------------------------
    # run_once
    # ------------------------------------------------------------------

    async def run_once(self) -> RunResult:
        """Build and run one agent attempt.

        Returns when any component exits or fails.
        Cancels sibling tasks and cleans up before returning.

        Raises:
            BuildFailure: if component construction raises. Never retried.
            asyncio.CancelledError: on external cancellation (re-raised after
                cancelling all tasks and running cleanup).
        """
        config = self._config
        source: IQSource | None = None
        transport: Transport | None = None

        try:
            # ---- Build phase -----------------------------------------------
            # Any exception here (including source.start()) is a BuildFailure.
            # We do not retry build failures.
            connected_event = asyncio.Event()

            def _on_connected() -> None:
                print("[rf-agent] connected", file=sys.stderr)
                connected_event.set()

            try:
                transport = self._factories.make_transport(config)
                codec = self._factories.make_codec(config)
                session = self._factories.make_session(
                    config, transport, codec, _on_connected
                )
                source = self._factories.make_source(config)
                processor = self._factories.make_processor(config)
                metrics = MetricsCollector()
                telemetry = self._factories.make_telemetry(
                    config, session, metrics, transport, codec
                )
                await source.start()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise BuildFailure("Failed to construct agent components") from exc

            # ---- Queues (fresh for every attempt) --------------------------
            iq_queue: asyncio.Queue[bytes] = asyncio.Queue(
                maxsize=config.queues.iq_queue_size
            )
            frame_queue: asyncio.Queue[SpectrumFrame] = asyncio.Queue(
                maxsize=config.queues.frame_queue_size
            )

            # ---- Task launch -----------------------------------------------
            tasks = [
                asyncio.create_task(source.run(iq_queue), name="source"),
                asyncio.create_task(
                    processor.run(iq_queue, frame_queue), name="processor"
                ),
                asyncio.create_task(session.run(frame_queue), name="session"),
                asyncio.create_task(telemetry.run(), name="telemetry"),
            ]

            first_error: BaseException | None = None

            try:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )

                # Capture first non-cancellation error from finished tasks
                for t in done:
                    if not t.cancelled():
                        task_exc = t.exception()
                        if task_exc is not None and first_error is None:
                            first_error = task_exc

                # Cancel siblings
                for t in pending:
                    t.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            except asyncio.CancelledError:
                # External cancellation: cancel everything, then re-raise
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            return RunResult(
                reason=(
                    RunStopReason.COMPONENT_FAILURE
                    if first_error is not None
                    else RunStopReason.NORMAL_EXIT
                ),
                error=first_error,
                connected=connected_event.is_set(),
            )

        finally:
            # Cleanup runs on every exit path (success, failure, cancellation)
            if source is not None:
                with contextlib.suppress(Exception):
                    await source.stop()
            if transport is not None:
                with contextlib.suppress(Exception):
                    await transport.close()

    # ------------------------------------------------------------------
    # run_forever
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Run the agent, retrying after each failed attempt.

        Backoff:
            Starts at reconnect.initial_delay_s, multiplied by
            reconnect.backoff_factor after each attempt, capped at
            reconnect.max_delay_s. Jitter (±50 %) is applied when
            reconnect.jitter is True.

        Stops immediately on:
            - asyncio.CancelledError (external cancellation)
            - BuildFailure (component construction error — do not retry)
        """
        reconnect = self._config.reconnect
        delay = reconnect.initial_delay_s

        while True:
            try:
                result = await self.run_once()
            except BuildFailure:
                raise
            except asyncio.CancelledError:
                raise

            if result.error is not None:
                if isinstance(result.error, (AuthenticationError, FatalSessionError)):
                    raise result.error
                print(
                    f"[rf-agent] disconnected"
                    f" ({type(result.error).__name__}): {result.error}",
                    file=sys.stderr,
                )

            if result.connected:
                delay = reconnect.initial_delay_s

            sleep_duration = delay
            if reconnect.jitter:
                sleep_duration = delay * (0.5 + random.random() * 0.5)

            print(
                f"[rf-agent] reconnecting in {sleep_duration:.1f}s...",
                file=sys.stderr,
            )
            await self._sleep(sleep_duration)

            delay = min(
                delay * reconnect.backoff_factor,
                reconnect.max_delay_s,
            )
