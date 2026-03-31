"""Telemetry package — heartbeat and agent_status producers.

Public surface:
  MetricsCollector  — accumulates gauges and drop counters; produces snapshots
  TelemetryLoop     — drives periodic emission via SessionView / TelemetrySender
  SessionView       — protocol: state + session_id (read-only)
  TelemetrySender   — protocol: send_heartbeat / send_agent_status
"""

from __future__ import annotations

from agent.telemetry.loop import SessionView, TelemetrySender, TelemetryLoop
from agent.telemetry.metrics import MetricsCollector

__all__ = [
    "MetricsCollector",
    "SessionView",
    "TelemetrySender",
    "TelemetryLoop",
]
