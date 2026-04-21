"""Unit tests for PipelineTiming."""

from __future__ import annotations

from agent.telemetry.stage_timing import PipelineTiming


def test_snapshot_returns_none_with_no_data() -> None:
    pt = PipelineTiming()
    assert pt.snapshot() is None


def test_snapshot_returns_data_when_any_stage_has_data() -> None:
    pt = PipelineTiming()
    pt.record_fft_ms(1.0)
    pt.record_encode_send_ms(2.0)
    # parse_iq has no data, but other stages do — snapshot must still be non-None
    snap = pt.snapshot()
    assert snap is not None
    assert snap.parse_iq_p50_ms == 0.0
    assert snap.fft_p50_ms == 1.0
    assert snap.encode_send_p50_ms == 2.0


def test_p50_p99_with_known_values() -> None:
    pt = PipelineTiming(window=200)
    # Feed 100 values: 1.0, 2.0, ..., 100.0
    for v in range(1, 101):
        pt.record_parse_iq_ms(float(v))
        pt.record_fft_ms(float(v) * 0.1)
        pt.record_encode_send_ms(float(v) * 2.0)

    snap = pt.snapshot()
    assert snap is not None

    # p50 index = int(100 * 50 / 100) = 50 → sorted[50] = 51.0
    assert snap.parse_iq_p50_ms == 51.0
    # p99 index = int(100 * 99 / 100) = 99 → sorted[99] = 100.0
    assert snap.parse_iq_p99_ms == 100.0

    assert abs(snap.fft_p50_ms - 5.1) < 1e-9
    assert abs(snap.fft_p99_ms - 10.0) < 1e-9

    assert snap.encode_send_p50_ms == 102.0
    assert snap.encode_send_p99_ms == 200.0


def test_window_eviction() -> None:
    window = 5
    pt = PipelineTiming(window=window)
    # Fill window + 1; oldest value (1.0) should be evicted
    for v in range(1, window + 2):
        pt.record_parse_iq_ms(float(v))

    snap = pt.snapshot()
    assert snap is not None
    # Remaining values: 2, 3, 4, 5, 6 — p50 = sorted[int(5*50/100)] = sorted[2] = 4.0
    assert snap.parse_iq_p50_ms == 4.0
    # p99 = sorted[int(5*99/100)] = sorted[4] = 6.0
    assert snap.parse_iq_p99_ms == 6.0


def test_queue_depth_avg() -> None:
    pt = PipelineTiming()
    pt.record_parse_iq_ms(1.0)  # ensure snapshot is non-None

    pt.record_iq_queue_depth(0)
    pt.record_iq_queue_depth(4)
    pt.record_iq_queue_depth(8)
    pt.record_frame_queue_depth(2)
    pt.record_frame_queue_depth(6)

    snap = pt.snapshot()
    assert snap is not None
    assert abs(snap.iq_queue_depth_avg - 4.0) < 1e-9
    assert abs(snap.frame_queue_depth_avg - 4.0) < 1e-9


def test_queue_depth_avg_zero_when_no_samples() -> None:
    pt = PipelineTiming()
    pt.record_parse_iq_ms(1.0)

    snap = pt.snapshot()
    assert snap is not None
    assert snap.iq_queue_depth_avg == 0.0
    assert snap.frame_queue_depth_avg == 0.0


def test_single_sample_p50_and_p99_equal() -> None:
    pt = PipelineTiming()
    pt.record_parse_iq_ms(42.0)
    pt.record_fft_ms(7.0)
    pt.record_encode_send_ms(3.5)

    snap = pt.snapshot()
    assert snap is not None
    assert snap.parse_iq_p50_ms == 42.0
    assert snap.parse_iq_p99_ms == 42.0
    assert snap.fft_p50_ms == 7.0
    assert snap.encode_send_p50_ms == 3.5
