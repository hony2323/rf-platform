"""PipelineTiming — per-stage latency and queue depth collector.

A single shared instance is injected into IQProcessor and Session.
Each stage records timings into a fixed-size rolling window (deque).
snapshot() computes p50/p99 for latency stages and mean for queue depths.
"""

from __future__ import annotations

from collections import deque

from agent.domain import PipelineLatencies


def _percentile(data: deque[float], p: float) -> float:
    """Return the p-th percentile of data (0–100). data must be non-empty."""
    sorted_data = sorted(data)
    idx = max(0, min(int(len(sorted_data) * p / 100), len(sorted_data) - 1))
    return sorted_data[idx]


def _mean(data: deque[float]) -> float:
    return sum(data) / len(data)


class PipelineTiming:
    """Rolling-window latency and queue depth collector for pipeline stages.

    Thread-safety: not thread-safe; designed for single-task asyncio use.
    """

    def __init__(self, window: int = 200) -> None:
        self._parse_iq_ms: deque[float] = deque(maxlen=window)
        self._fft_ms: deque[float] = deque(maxlen=window)
        self._encode_send_ms: deque[float] = deque(maxlen=window)
        self._iq_queue_depth: deque[float] = deque(maxlen=window)
        self._frame_queue_depth: deque[float] = deque(maxlen=window)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_parse_iq_ms(self, ms: float) -> None:
        self._parse_iq_ms.append(ms)

    def record_fft_ms(self, ms: float) -> None:
        self._fft_ms.append(ms)

    def record_encode_send_ms(self, ms: float) -> None:
        self._encode_send_ms.append(ms)

    def record_iq_queue_depth(self, depth: int) -> None:
        self._iq_queue_depth.append(float(depth))

    def record_frame_queue_depth(self, depth: int) -> None:
        self._frame_queue_depth.append(float(depth))

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> PipelineLatencies | None:
        """Return a snapshot of current metrics, or None if no stage has data yet."""
        if not any(
            [
                self._parse_iq_ms,
                self._fft_ms,
                self._encode_send_ms,
                self._iq_queue_depth,
                self._frame_queue_depth,
            ]
        ):
            return None
        return PipelineLatencies(
            parse_iq_p50_ms=(
                _percentile(self._parse_iq_ms, 50) if self._parse_iq_ms else 0.0
            ),
            parse_iq_p99_ms=(
                _percentile(self._parse_iq_ms, 99) if self._parse_iq_ms else 0.0
            ),
            fft_p50_ms=_percentile(self._fft_ms, 50) if self._fft_ms else 0.0,
            fft_p99_ms=_percentile(self._fft_ms, 99) if self._fft_ms else 0.0,
            encode_send_p50_ms=(
                _percentile(self._encode_send_ms, 50) if self._encode_send_ms else 0.0
            ),
            encode_send_p99_ms=(
                _percentile(self._encode_send_ms, 99) if self._encode_send_ms else 0.0
            ),
            iq_queue_depth_avg=(
                _mean(self._iq_queue_depth) if self._iq_queue_depth else 0.0
            ),
            frame_queue_depth_avg=(
                _mean(self._frame_queue_depth) if self._frame_queue_depth else 0.0
            ),
        )
