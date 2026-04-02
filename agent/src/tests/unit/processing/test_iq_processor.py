"""Unit tests for IQProcessor — orchestration contract.

Focus: byte remainder carryover, sample accumulation, FFT dispatch timing,
configure() flush semantics, error counting, and async run() behaviour.

parse_iq and FFTProcessor.process are mocked in every test except the final
real-wiring smoke test (Batch 4).
"""

from __future__ import annotations

import asyncio
import struct

import numpy as np
import pytest

from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    SpectrumFrame,
)
from agent.processing.parse_iq import IQParseError, IQParseErrorCode, IQParseResult
from agent.processing.processor import IQProcessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RATE = 48_000
_CENTER_FREQ = 100_000_000
_TIMESTAMP = "2026-03-31T00:00:00+00:00"


def make_descriptor(**overrides: object) -> IQDescriptor:
    defaults: dict[str, object] = {
        "sample_format": SampleFormat.FLOAT32,
        "endianness": Endianness.LITTLE,
        "layout": Layout.INTERLEAVED,
        "sample_rate_hz": _SAMPLE_RATE,
        "center_freq_hz": _CENTER_FREQ,
        "dc_offset_remove": False,
        "normalize": False,
    }
    defaults.update(overrides)
    return IQDescriptor(**defaults)  # type: ignore[arg-type]


def make_rf_config(fft_size: int = 4, bin_count: int | None = None) -> RFConfig:
    return RFConfig(
        center_freq_hz=_CENTER_FREQ,
        sample_rate_hz=_SAMPLE_RATE,
        fft_size=fft_size,
        bin_count=bin_count,
    )


def encode_float32_iq(samples: list[float]) -> bytes:
    """Encode a flat list of floats as interleaved float32 LE IQ bytes."""
    return struct.pack(f"<{len(samples)}f", *samples)


def make_parse_result(sample_count: int, start: float = 0.0) -> IQParseResult:
    """Return an IQParseResult with `sample_count` interleaved float32 IQ samples.

    Values are sequential starting at `start` (step 0.01) so individual
    test windows are distinguishable when debugging.
    """
    values = [start + i * 0.01 for i in range(sample_count * 2)]
    return IQParseResult(
        samples=np.array(values, dtype=np.float32),
        sample_count=sample_count,
    )


def _fake_frame(fft_size: int = 4, timestamp: str = _TIMESTAMP) -> SpectrumFrame:
    payload = struct.pack(f"<{fft_size}f", *([0.0] * fft_size))
    return SpectrumFrame(payload=payload, timestamp_utc=timestamp, bin_count=fft_size)


# ---------------------------------------------------------------------------
# Batch 1 — remainder handling and sample accumulation basics
# ---------------------------------------------------------------------------


def test_push_returns_no_frame_when_chunk_contains_only_remainder_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sub-sample-size chunk must be stored as remainder;
    parse_iq must not be called."""
    import agent.processing.processor as mod

    parse_calls: list[bytes] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        parse_calls.append(buffer)
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4))
    bps = SampleFormat.FLOAT32.bytes_per_sample  # 8

    frames = proc.push(b"\x00" * (bps - 1), _TIMESTAMP)

    assert frames == []
    assert len(parse_calls) == 0
    assert proc._remainder == b"\x00" * (bps - 1)


def test_push_prepends_previous_remainder_and_parses_once_data_becomes_sample_aligned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remainder bytes from push N are
    prepended to push N+1 before parse_iq is called."""
    import agent.processing.processor as mod

    captured_buffers: list[bytes] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        captured_buffers.append(buffer)
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4))
    bps = SampleFormat.FLOAT32.bytes_per_sample  # 8
    partial_len = bps - 3  # 5 bytes — one incomplete sample

    # Push 1: sub-sample bytes only — stored as remainder, parse not called
    proc.push(b"\xaa" * partial_len, _TIMESTAMP)
    assert len(captured_buffers) == 0

    # Push 2: 3 completing bytes → 5 + 3 = 8 = exactly one sample → parse called once
    completing = b"\xbb" * (bps - partial_len)
    proc.push(completing, _TIMESTAMP)

    assert len(captured_buffers) == 1
    assert len(captured_buffers[0]) == bps
    assert captured_buffers[0] == b"\xaa" * partial_len + b"\xbb" * (bps - partial_len)


def test_push_accumulates_parsed_samples_until_fft_size_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsed samples accumulate across pushes;
    FFT fires only once fft_size is reached."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_size = 4
    fft_calls: list[np.ndarray] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        fft_calls.append(samples.copy())
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size))

    # Push 2 complex samples (fft_size=4 requires 4) — should not trigger FFT
    frames1 = proc.push(encode_float32_iq([0.0] * (2 * 2)), _TIMESTAMP)
    assert frames1 == []
    assert len(fft_calls) == 0

    # Push 2 more — total 4 = fft_size → FFT fires, one frame emitted
    frames2 = proc.push(encode_float32_iq([0.0] * (2 * 2)), _TIMESTAMP)
    assert len(frames2) == 1
    assert len(fft_calls) == 1


def test_push_calls_fft_with_exactly_fft_size_complex_samples_not_all_buffered_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FFT receives exactly fft_size*2 floats
    even when the push delivered more samples."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_size = 4
    received_shapes: list[int] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        received_shapes.append(len(samples))
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size))

    # Push 6 complex samples — more than fft_size(4), so 1 frame + 2 leftover
    chunk = encode_float32_iq([0.1] * (6 * 2))
    frames = proc.push(chunk, _TIMESTAMP)

    assert len(frames) == 1
    assert len(received_shapes) == 1
    assert (
        received_shapes[0] == fft_size * 2
    )  # exactly fft_size complex samples as floats


# ---------------------------------------------------------------------------
# Batch 2 — leftover samples, multi-frame push, configure() flush
# ---------------------------------------------------------------------------


def test_push_keeps_leftover_samples_for_next_frame_after_first_fft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Samples beyond fft_size carry forward; second push completes the next frame."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_size = 4

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size))

    # Push fft_size(4) + 2 extra complex samples → 1 frame, 2 leftover
    chunk1 = encode_float32_iq([0.0] * ((fft_size + 2) * 2))
    frames1 = proc.push(chunk1, _TIMESTAMP)
    assert len(frames1) == 1

    # Push 2 more complex samples → 2 leftover + 2 new = fft_size → second frame
    chunk2 = encode_float32_iq([0.0] * (2 * 2))
    frames2 = proc.push(chunk2, _TIMESTAMP)
    assert len(frames2) == 1


def test_push_emits_multiple_frames_when_single_push_contains_multiple_fft_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single push with 2×fft_size complex samples emits exactly 2 frames."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_size = 4

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size))

    chunk = encode_float32_iq([0.0] * (fft_size * 2 * 2))  # 2 full FFT windows
    frames = proc.push(chunk, _TIMESTAMP)
    assert len(frames) == 2


def test_configure_clears_sample_accumulation_and_byte_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure() must flush both the byte remainder
    and the sample accumulation buffer."""
    import agent.processing.processor as mod

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4))
    bps = SampleFormat.FLOAT32.bytes_per_sample

    # Accumulate 2 parsed complex samples
    proc.push(encode_float32_iq([0.0] * (2 * 2)), _TIMESTAMP)
    assert proc._sample_count == 2

    # Leave a byte remainder
    proc.push(b"\xff" * (bps - 1), _TIMESTAMP)
    assert len(proc._remainder) == bps - 1

    proc.configure(make_rf_config(fft_size=4))

    assert proc._remainder == b""
    assert proc._sample_count == 0
    assert proc._sample_buf == []


def test_push_after_configure_uses_new_fft_size_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After configure(), the new fft_size governs frame emission;
    old buffer is gone."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_calls: list[np.ndarray] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        fft_calls.append(samples.copy())
        return _fake_frame(len(samples) // 2, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    # Start with fft_size=8; accumulate 4 samples (below threshold)
    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=8))
    proc.push(encode_float32_iq([0.0] * (4 * 2)), _TIMESTAMP)
    assert len(fft_calls) == 0

    # Reconfigure to fft_size=4 — old 4-sample buffer must be discarded
    proc.configure(make_rf_config(fft_size=4))

    # Push exactly 4 samples under new config → 1 frame, FFT gets 4*2 floats
    frames = proc.push(encode_float32_iq([0.0] * (4 * 2)), _TIMESTAMP)
    assert len(frames) == 1
    assert len(fft_calls) == 1
    assert len(fft_calls[0]) == 4 * 2  # new fft_size=4


# ---------------------------------------------------------------------------
# Batch 3 — error counting, run() async contract
# ---------------------------------------------------------------------------


def test_push_increments_parse_error_count_and_drops_data_when_parse_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parse_error_count increments on each IQParseError; no frame is emitted."""
    import agent.processing.processor as mod

    monkeypatch.setattr(
        mod,
        "parse_iq",
        lambda *_: IQParseError(
            code=IQParseErrorCode.UNSUPPORTED_FORMAT, message="injected"
        ),
    )

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4))
    assert proc.parse_error_count == 0

    frames = proc.push(encode_float32_iq([0.0] * (4 * 2)), _TIMESTAMP)
    assert frames == []
    assert proc.parse_error_count == 1

    proc.push(encode_float32_iq([0.0] * (4 * 2)), _TIMESTAMP)
    assert proc.parse_error_count == 2


def test_push_does_not_increment_parse_error_count_when_chunk_only_forms_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parse_iq is never called for sub-sample chunks, so parse_error_count stays 0."""
    import agent.processing.processor as mod

    parse_calls: list[bytes] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseError:
        parse_calls.append(buffer)
        return IQParseError(
            code=IQParseErrorCode.EMPTY_BUFFER, message="should not be called"
        )

    monkeypatch.setattr(mod, "parse_iq", fake_parse)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4))
    bps = SampleFormat.FLOAT32.bytes_per_sample

    proc.push(b"\x00" * (bps - 1), _TIMESTAMP)

    assert len(parse_calls) == 0
    assert proc.parse_error_count == 0


async def test_run_drains_iq_queue_and_enqueues_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() dequeues IQ chunks and forwards produced SpectrumFrames to frame_queue."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_size = 4

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size))
    iq_q: asyncio.Queue[bytes] = asyncio.Queue()
    frame_q: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    # Enqueue exactly one FFT window
    await iq_q.put(encode_float32_iq([0.0] * (fft_size * 2)))

    task = asyncio.create_task(proc.run(iq_q, frame_q))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert frame_q.qsize() == 1
    frame = frame_q.get_nowait()
    assert frame.bin_count == fft_size


async def test_run_uses_chunk_dequeue_time_as_timestamp_input_to_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() timestamps each chunk using datetime.now(UTC) at the moment of dequeue."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline
    from datetime import datetime, timezone

    fft_size = 4
    fixed_dt = datetime(2026, 3, 31, 12, 0, 0, tzinfo=timezone.utc)
    fixed_ts = fixed_dt.isoformat()

    class _FixedDatetime:
        @staticmethod
        def now(tz: object = None) -> datetime:
            return fixed_dt

    monkeypatch.setattr(mod, "datetime", _FixedDatetime)

    observed_timestamps: list[str] = []

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: np.ndarray, ts: str) -> SpectrumFrame:
        observed_timestamps.append(ts)
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size))
    iq_q: asyncio.Queue[bytes] = asyncio.Queue()
    frame_q: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    await iq_q.put(encode_float32_iq([0.0] * (fft_size * 2)))

    task = asyncio.create_task(proc.run(iq_q, frame_q))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(observed_timestamps) == 1
    assert observed_timestamps[0] == fixed_ts


# ---------------------------------------------------------------------------
# Batch 3b — PipelineTiming injection
# ---------------------------------------------------------------------------


def test_processor_records_parse_iq_timing_when_timings_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parse_iq timing is recorded for each push that reaches parse_iq."""
    import agent.processing.processor as mod
    from agent.telemetry.stage_timing import PipelineTiming

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)

    timings = PipelineTiming()
    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4), timings=timings)

    # Two pushes that reach parse_iq
    proc.push(encode_float32_iq([0.0] * (2 * 2)), _TIMESTAMP)
    proc.push(encode_float32_iq([0.0] * (2 * 2)), _TIMESTAMP)

    snap = timings.snapshot()
    assert snap is not None
    assert snap.parse_iq_p50_ms >= 0.0


def test_processor_records_fft_timing_when_timings_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FFT timing is recorded once per frame emitted."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline
    from agent.telemetry.stage_timing import PipelineTiming

    fft_size = 4

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: object, ts: str) -> SpectrumFrame:
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    timings = PipelineTiming()
    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size), timings=timings)

    # Push exactly one FFT window — one frame emitted, one FFT timing recorded
    frames = proc.push(encode_float32_iq([0.0] * (fft_size * 2)), _TIMESTAMP)
    assert len(frames) == 1

    snap = timings.snapshot()
    assert snap is not None
    assert snap.fft_p50_ms >= 0.0


def test_processor_increments_metrics_parse_errors_when_metrics_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parse_error_count and MetricsCollector.parse_errors stay in sync."""
    import agent.processing.processor as mod
    from agent.telemetry.metrics import MetricsCollector

    monkeypatch.setattr(
        mod,
        "parse_iq",
        lambda *_: IQParseError(code=IQParseErrorCode.UNSUPPORTED_FORMAT, message="x"),
    )

    mc = MetricsCollector()
    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=4), metrics=mc)

    proc.push(encode_float32_iq([0.0] * (4 * 2)), _TIMESTAMP)
    proc.push(encode_float32_iq([0.0] * (4 * 2)), _TIMESTAMP)

    assert proc.parse_error_count == 2
    snap = mc.snapshot()
    assert snap.drops.parse_errors == 2


def test_processor_works_without_timings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IQProcessor with timings=None behaves identically to the default."""
    import agent.processing.processor as mod
    from agent.processing import fft_pipeline

    fft_size = 4

    def fake_parse(descriptor: IQDescriptor, buffer: bytes) -> IQParseResult:
        return make_parse_result(len(buffer) // descriptor.bytes_per_sample)

    def fake_fft_process(self: object, samples: object, ts: str) -> SpectrumFrame:
        return _fake_frame(fft_size, ts)

    monkeypatch.setattr(mod, "parse_iq", fake_parse)
    monkeypatch.setattr(fft_pipeline.FFTProcessor, "process", fake_fft_process)

    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=fft_size), timings=None)
    frames = proc.push(encode_float32_iq([0.0] * (fft_size * 2)), _TIMESTAMP)
    assert len(frames) == 1


# ---------------------------------------------------------------------------
# Batch 4 — real-wiring smoke test (no mocks)
# ---------------------------------------------------------------------------


def test_push_with_real_parser_and_real_fft_emits_one_real_spectrum_frame_when_enough_float32_iq_arrives() -> (  # noqa: E501
    None
):
    """Full pipeline (real parse_iq + real FFTProcessor):
    float32 IQ → valid SpectrumFrame."""
    fft_size = 4
    proc = IQProcessor(
        make_descriptor(normalize=True),
        make_rf_config(fft_size=fft_size),
    )

    # Interleaved float32 LE: alternating I=0.5, Q=-0.5 for fft_size complex samples
    values = [0.5 if i % 2 == 0 else -0.5 for i in range(fft_size * 2)]
    chunk = encode_float32_iq(values)

    frames = proc.push(chunk, _TIMESTAMP)

    assert len(frames) == 1
    frame = frames[0]
    assert frame.bin_count == fft_size
    assert len(frame.payload) == fft_size * 4
    assert frame.timestamp_utc == _TIMESTAMP

    powers = np.frombuffer(frame.payload, dtype="<f4")
    assert len(powers) == fft_size
    assert np.all(np.isfinite(powers))
