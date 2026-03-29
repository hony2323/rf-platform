"""Agent orchestrator — composition and lifecycle.

This is the top-level entry point. It wires components together,
starts concurrent tasks, and handles shutdown.

It does no real work itself — only composes.
"""

from __future__ import annotations

from typing import Protocol

from agent.config import AgentConfig


class AgentRuntime(Protocol):
    """Top-level agent lifecycle."""

    async def run(self) -> None:
        """Start the agent. Runs until shutdown signal.

        Wiring:
            source = create_source(config)
            processor = create_processor(config)
            transport = create_transport(config)
            session = create_session(transport, codec, config)
            telemetry = create_telemetry(session, config)

            iq_queue = asyncio.Queue(maxsize=config.queues.iq_queue_size)
            frame_queue = asyncio.Queue(maxsize=config.queues.frame_queue_size)

            run concurrently:
                source.run(output=iq_queue)
                processor.run(input=iq_queue, output=frame_queue)
                session.run(frame_queue=frame_queue)
                telemetry.run()

        Shutdown:
            On SIGINT/SIGTERM → cancel all tasks → close transport → stop source.
        """
        ...

    async def shutdown(self) -> None:
        """Graceful shutdown. Cancel tasks, close connections, release hardware."""
        ...
