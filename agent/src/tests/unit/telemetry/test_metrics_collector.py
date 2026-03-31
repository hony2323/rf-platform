"""Unit tests for MetricsCollector."""

from __future__ import annotations

import pytest

from agent.telemetry.metrics import MetricsCollector


def test_metrics_collector_snapshot_reports_queue_depth_fill_and_tx_rate() -> None:
    mc = MetricsCollector()
    mc.set_cpu_usage_pct(42.5)
    mc.set_throttled(True)
    mc.set_tx_bytes_per_sec(1024)
    mc.set_queue_depth(8)
    mc.set_queue_fill_pct(0.75)
    mc.inc_local_throttle(3)
    mc.inc_queue_overflow(2)
    mc.inc_server_rejected(1)

    snap = mc.snapshot()

    assert snap.cpu_usage_pct == 42.5
    assert snap.throttled is True
    assert snap.tx_bytes_per_sec == 1024
    assert snap.queue_depth == 8
    assert snap.queue_fill_pct == 0.75
    assert snap.drops.local_throttle == 3
    assert snap.drops.queue_overflow == 2
    assert snap.drops.server_rejected == 1


def test_metrics_collector_resets_drop_counters_after_snapshot() -> None:
    mc = MetricsCollector()
    mc.inc_local_throttle(5)
    mc.inc_queue_overflow(3)
    mc.inc_server_rejected(2)

    first = mc.snapshot()
    second = mc.snapshot()

    assert first.drops.local_throttle == 5
    assert first.drops.queue_overflow == 3
    assert first.drops.server_rejected == 2

    assert second.drops.local_throttle == 0
    assert second.drops.queue_overflow == 0
    assert second.drops.server_rejected == 0


def test_metrics_collector_keeps_non_drop_metrics_available_after_reset() -> None:
    mc = MetricsCollector()
    mc.set_cpu_usage_pct(10.0)
    mc.set_throttled(False)
    mc.set_tx_bytes_per_sec(512)
    mc.set_queue_depth(4)
    mc.set_queue_fill_pct(0.5)

    first = mc.snapshot()
    second = mc.snapshot()

    for snap in (first, second):
        assert snap.cpu_usage_pct == 10.0
        assert snap.throttled is False
        assert snap.tx_bytes_per_sec == 512
        assert snap.queue_depth == 4
        assert snap.queue_fill_pct == 0.5

    assert first.drops.local_throttle == 0
    assert second.drops.local_throttle == 0


def test_metrics_collector_accumulates_multiple_drop_increments() -> None:
    mc = MetricsCollector()
    mc.inc_local_throttle(2)
    mc.inc_local_throttle(3)
    mc.inc_local_throttle()

    snap = mc.snapshot()

    assert snap.drops.local_throttle == 6


def test_metrics_collector_defaults_to_zero_or_false_values() -> None:
    mc = MetricsCollector()
    snap = mc.snapshot()

    assert snap.cpu_usage_pct == 0.0
    assert snap.throttled is False
    assert snap.tx_bytes_per_sec == 0
    assert snap.queue_depth == 0
    assert snap.queue_fill_pct == 0.0
    assert snap.drops.local_throttle == 0
    assert snap.drops.queue_overflow == 0
    assert snap.drops.server_rejected == 0


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


def test_metrics_collector_rejects_negative_drop_increment() -> None:
    mc = MetricsCollector()
    with pytest.raises(ValueError):
        mc.inc_local_throttle(-1)
    with pytest.raises(ValueError):
        mc.inc_queue_overflow(-1)
    with pytest.raises(ValueError):
        mc.inc_server_rejected(-1)


def test_metrics_collector_rejects_negative_queue_depth() -> None:
    mc = MetricsCollector()
    with pytest.raises(ValueError):
        mc.set_queue_depth(-1)


def test_metrics_collector_rejects_negative_tx_bytes_per_sec() -> None:
    mc = MetricsCollector()
    with pytest.raises(ValueError):
        mc.set_tx_bytes_per_sec(-1)


def test_metrics_collector_rejects_queue_fill_pct_out_of_range() -> None:
    mc = MetricsCollector()
    with pytest.raises(ValueError):
        mc.set_queue_fill_pct(-0.1)
    with pytest.raises(ValueError):
        mc.set_queue_fill_pct(100.1)


def test_metrics_collector_accepts_boundary_queue_fill_pct() -> None:
    mc = MetricsCollector()
    mc.set_queue_fill_pct(0.0)
    mc.set_queue_fill_pct(100.0)
