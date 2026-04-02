"""MetricsCollector — owns gauges and drop counters for agent_status messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.domain import AgentMetrics, DropCounters

if TYPE_CHECKING:
    from agent.telemetry.stage_timing import PipelineTiming


class MetricsCollector:
    """Accumulates runtime metrics; produces AgentMetrics snapshots.

    Gauges hold the latest value set. Drop counters accumulate until
    snapshot() is called, at which point only drops are reset to zero.
    """

    def __init__(self, timings: PipelineTiming | None = None) -> None:
        self._cpu_usage_pct: float = 0.0
        self._throttled: bool = False
        self._tx_bytes_per_sec: int = 0
        self._queue_depth: int = 0
        self._queue_fill_pct: float = 0.0

        self._local_throttle: int = 0
        self._queue_overflow: int = 0
        self._server_rejected: int = 0
        self._parse_errors: int = 0

        self._timings = timings

    # ------------------------------------------------------------------
    # Gauge setters
    # ------------------------------------------------------------------

    def set_cpu_usage_pct(self, value: float) -> None:
        self._cpu_usage_pct = value

    def set_throttled(self, value: bool) -> None:
        self._throttled = value

    def set_tx_bytes_per_sec(self, value: int) -> None:
        if value < 0:
            raise ValueError(f"tx_bytes_per_sec must be >= 0, got {value!r}")
        self._tx_bytes_per_sec = value

    def set_queue_depth(self, value: int) -> None:
        if value < 0:
            raise ValueError(f"queue_depth must be >= 0, got {value!r}")
        self._queue_depth = value

    def set_queue_fill_pct(self, value: float) -> None:
        if not (0.0 <= value <= 100.0):
            raise ValueError(f"queue_fill_pct must be in [0, 100], got {value!r}")
        self._queue_fill_pct = value

    # ------------------------------------------------------------------
    # Drop counter incrementers
    # ------------------------------------------------------------------

    def inc_local_throttle(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count!r}")
        self._local_throttle += count

    def inc_queue_overflow(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count!r}")
        self._queue_overflow += count

    def inc_server_rejected(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count!r}")
        self._server_rejected += count

    def inc_parse_errors(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError(f"count must be >= 0, got {count!r}")
        self._parse_errors += count

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> AgentMetrics:
        """Return current metrics and reset drop counters to zero.

        Gauges are NOT reset — they retain the last set value.
        """
        metrics = AgentMetrics(
            cpu_usage_pct=self._cpu_usage_pct,
            throttled=self._throttled,
            tx_bytes_per_sec=self._tx_bytes_per_sec,
            queue_depth=self._queue_depth,
            queue_fill_pct=self._queue_fill_pct,
            drops=DropCounters(
                local_throttle=self._local_throttle,
                queue_overflow=self._queue_overflow,
                server_rejected=self._server_rejected,
                parse_errors=self._parse_errors,
            ),
            pipeline=self._timings.snapshot() if self._timings is not None else None,
        )
        self._local_throttle = 0
        self._queue_overflow = 0
        self._server_rejected = 0
        self._parse_errors = 0
        return metrics
