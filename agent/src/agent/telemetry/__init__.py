"""Telemetry interface — heartbeat and agent_status producers.

Runs on timers. Produces messages and hands them to session for sending.
"""

from __future__ import annotations

from typing import Protocol

from agent.domain import AgentMetrics, DropCounters


class MetricsCollector(Protocol):
    """Collects runtime metrics for agent_status messages."""

    def snapshot(self) -> AgentMetrics:
        """Capture current metrics. Called periodically by telemetry."""
        ...

    def record_drop(self, reason: str) -> None:
        """Increment a drop counter.

        reason: "local_throttle" | "queue_overflow" | "server_rejected"
        """
        ...

    def reset_drops(self) -> DropCounters:
        """Return current drop counters and reset them to zero.

        Called after each agent_status send so counters reflect
        drops since last report.
        """
        ...


class Telemetry(Protocol):
    """Manages periodic heartbeat and status reporting."""

    async def run(self) -> None:
        """Start periodic heartbeat and agent_status timers.

        Runs until cancelled. Only sends when session state allows it.
        Heartbeat interval and status interval are configurable.
        """
        ...
