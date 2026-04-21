"""Unit tests for session/bandwidth.py — BandwidthLimiter implementations."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.session.bandwidth import DecimateLimiter, DropLimiter, make_limiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_time(t: float):
    """Return a context manager that fixes asyncio loop time to `t`."""
    return patch(
        "agent.session.bandwidth.asyncio.get_event_loop",
        return_value=type("FakeLoop", (), {"time": staticmethod(lambda: t)})(),
    )


# ---------------------------------------------------------------------------
# DecimateLimiter
# ---------------------------------------------------------------------------


class TestDecimateLimiter:
    def test_first_frame_always_passes(self) -> None:
        limiter = DecimateLimiter(max_bytes_per_sec=1000)
        with _patch_time(0.0):
            assert limiter.should_send(500) is True

    def test_frame_arriving_too_early_is_dropped(self) -> None:
        limiter = DecimateLimiter(max_bytes_per_sec=1000)
        # First frame at t=0 passes; interval = 500/1000 = 0.5 s
        with _patch_time(0.0):
            assert limiter.should_send(500) is True
        # t=0.1 — interval hasn't elapsed yet
        with _patch_time(0.1):
            assert limiter.should_send(500) is False

    def test_frame_after_interval_passes(self) -> None:
        limiter = DecimateLimiter(max_bytes_per_sec=1000)
        # First frame at t=0 passes; interval = 500/1000 = 0.5 s
        with _patch_time(0.0):
            assert limiter.should_send(500) is True
        # t=0.5 — exactly at the boundary, should pass
        with _patch_time(0.5):
            assert limiter.should_send(500) is True

    def test_interval_adapts_to_frame_size(self) -> None:
        limiter = DecimateLimiter(max_bytes_per_sec=1000)
        # First frame: 200 bytes → interval = 0.2 s
        with _patch_time(0.0):
            assert limiter.should_send(200) is True
        # At t=0.15 the interval hasn't elapsed
        with _patch_time(0.15):
            assert limiter.should_send(200) is False
        # At t=0.2 it has elapsed
        with _patch_time(0.2):
            assert limiter.should_send(200) is True

    def test_large_frame_extends_next_window(self) -> None:
        limiter = DecimateLimiter(max_bytes_per_sec=1000)
        # First frame: 1000 bytes → interval = 1.0 s
        with _patch_time(0.0):
            assert limiter.should_send(1000) is True
        # At t=0.9 it's still suppressed
        with _patch_time(0.9):
            assert limiter.should_send(500) is False
        # At t=1.0 it passes
        with _patch_time(1.0):
            assert limiter.should_send(500) is True


# ---------------------------------------------------------------------------
# DropLimiter
# ---------------------------------------------------------------------------


class TestDropLimiter:
    def test_allows_sends_while_bucket_has_tokens(self) -> None:
        limiter = DropLimiter(max_bytes_per_sec=1000)
        # Bucket starts full (1000 tokens). Two 400-byte frames → 800 total.
        with _patch_time(0.0):
            assert limiter.should_send(400) is True
            assert limiter.should_send(400) is True

    def test_drops_when_bucket_is_empty(self) -> None:
        limiter = DropLimiter(max_bytes_per_sec=1000)
        # Bucket = 1000. Send 1000 bytes → bucket = 0.
        with _patch_time(0.0):
            assert limiter.should_send(1000) is True
            # Next frame: bucket = 0, can't send 1 byte
            assert limiter.should_send(1) is False

    def test_bucket_refills_over_time(self) -> None:
        limiter = DropLimiter(max_bytes_per_sec=1000)
        # Drain the bucket
        with _patch_time(0.0):
            assert limiter.should_send(1000) is True
            assert limiter.should_send(1) is False
        # After 0.5 s, bucket should have ~500 tokens
        with _patch_time(0.5):
            assert limiter.should_send(400) is True

    def test_bucket_is_capped_at_max(self) -> None:
        limiter = DropLimiter(max_bytes_per_sec=1000)
        # At t=0 (first call), last_refill is set; bucket stays at 1000 max.
        with _patch_time(0.0):
            limiter.should_send(0)  # initialise last_refill without draining
        # After a very long gap, bucket should not exceed max_bytes_per_sec
        with _patch_time(100.0):
            # Should be capped at 1000, not 100000
            assert limiter.should_send(1000) is True
            # Bucket is now 0; next large send fails
            assert limiter.should_send(1) is False

    def test_initial_burst_up_to_one_second(self) -> None:
        """Bucket starts full — one second worth of data can burst at line rate."""
        limiter = DropLimiter(max_bytes_per_sec=500)
        with _patch_time(0.0):
            # 500-byte burst should pass immediately (bucket started at 500)
            assert limiter.should_send(500) is True
            # Now bucket is 0, even 1 byte is dropped
            assert limiter.should_send(1) is False


# ---------------------------------------------------------------------------
# make_limiter
# ---------------------------------------------------------------------------


class TestMakeLimiter:
    def test_returns_none_when_unlimited(self) -> None:
        assert make_limiter(None, "decimate") is None
        assert make_limiter(None, "drop") is None

    def test_returns_decimate_limiter(self) -> None:
        limiter = make_limiter(1000, "decimate")
        assert isinstance(limiter, DecimateLimiter)

    def test_returns_drop_limiter(self) -> None:
        limiter = make_limiter(1000, "drop")
        assert isinstance(limiter, DropLimiter)

    def test_raises_on_unknown_strategy(self) -> None:
        with pytest.raises(ValueError, match="Unknown bandwidth strategy"):
            make_limiter(1000, "invalid_strategy")
