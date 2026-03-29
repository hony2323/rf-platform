"""Unit tests for IQProcessor (parse_iq → accumulation → FFTProcessor pipeline)."""

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
from agent.processing.processor import IQProcessor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FFT_SIZE = 16  # small but valid; keeps test data tiny
_SAMPLE_RATE = 102_400
_CENTER_FREQ = 433_920_000
_TIMESTAMP = "2026-03-29T10:00:00.000Z"


def make_descriptor(**kwargs: object) -> IQDescriptor:
    defaults: dict[str, object] = {
        "sample_format": SampleFormat.FLOAT32,
        "endianness": Endianness.LITTLE,
        "layout": Layout.INTERLEAVED,
        "sample_rate_hz": _SAMPLE_RATE,
        "center_freq_hz": _CENTER_FREQ,
        "dc_offset_remove": False,
        "normalize": False,
    }
    defaults.update(kwargs)
    return IQDescriptor(**defaults)  # type: ignore[arg-type]


def make_rf_config(**kwargs: object) -> RFConfig:
    defaults: dict[str, object] = {
        "center_freq_hz": _CENTER_FREQ,
        "sample_rate_hz": _SAMPLE_RATE,
        "fft_size": _FFT_SIZE,
    }
    defaults.update(kwargs)
    return RFConfig(**defaults)  # type: ignore[arg-type]


def make_float32_iq_bytes(n_samples: int) -> bytes:
    """n_samples complex float32 LE samples (I=0.1, Q=0.05, constant)."""
    buf = np.empty(n_samples * 2, dtype="<f4")
    buf[0::2] = 0.1
    buf[1::2] = 0.05
    return buf.tobytes()


def unpack_payload(frame: SpectrumFrame) -> np.ndarray:
    n = frame.bin_count
    return np.array(struct.unpack(f"<{n}f", frame.payload), dtype=np.float32)


# ---------------------------------------------------------------------------
# push() — single chunk
# ---------------------------------------------------------------------------


def test_push_exactly_fft_size_samples_emits_one_frame() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    chunk = make_float32_iq_bytes(_FFT_SIZE)
    frames = proc.push(chunk, _TIMESTAMP)
    assert len(frames) == 1


def test_push_fewer_than_fft_size_samples_emits_no_frame() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    chunk = make_float32_iq_bytes(_FFT_SIZE - 1)
    frames = proc.push(chunk, _TIMESTAMP)
    assert len(frames) == 0


def test_push_two_fft_size_samples_emits_two_frames() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    chunk = make_float32_iq_bytes(_FFT_SIZE * 2)
    frames = proc.push(chunk, _TIMESTAMP)
    assert len(frames) == 2


def test_push_empty_bytes_emits_no_frame() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    frames = proc.push(b"", _TIMESTAMP)
    assert frames == []


# ---------------------------------------------------------------------------
# push() — sample accumulation across chunks
# ---------------------------------------------------------------------------


def test_push_accumulates_two_half_chunks_into_one_frame() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    half = _FFT_SIZE // 2
    frames1 = proc.push(make_float32_iq_bytes(half), _TIMESTAMP)
    assert frames1 == []  # not enough yet
    frames2 = proc.push(make_float32_iq_bytes(half), _TIMESTAMP)
    assert len(frames2) == 1


def test_push_accumulated_samples_cleared_after_frame_emitted() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    # Fill to exactly one frame
    proc.push(make_float32_iq_bytes(_FFT_SIZE), _TIMESTAMP)
    # Next partial push should NOT produce another frame immediately
    frames = proc.push(make_float32_iq_bytes(1), _TIMESTAMP)
    assert frames == []


def test_push_leftover_samples_carry_forward_to_next_frame() -> None:
    """1.5× fft_size samples: first push emits 1 frame, second emits 1 more."""
    proc = IQProcessor(make_descriptor(), make_rf_config())
    # Push 1.5× worth — emits 1 frame, holds 0.5× samples
    frames1 = proc.push(make_float32_iq_bytes(_FFT_SIZE + _FFT_SIZE // 2), _TIMESTAMP)
    assert len(frames1) == 1
    # Push the remaining half — should emit the second frame
    frames2 = proc.push(make_float32_iq_bytes(_FFT_SIZE // 2), _TIMESTAMP)
    assert len(frames2) == 1


# ---------------------------------------------------------------------------
# push() — byte remainder handling
# ---------------------------------------------------------------------------


def test_push_holds_partial_sample_bytes_as_remainder() -> None:
    """FLOAT32 = 8 bytes/sample. A chunk with 3 extra bytes is held."""
    proc = IQProcessor(make_descriptor(), make_rf_config())
    bps = SampleFormat.FLOAT32.bytes_per_sample  # 8
    extra = b"\x00" * (bps - 1)  # 7 incomplete bytes
    chunk = make_float32_iq_bytes(_FFT_SIZE) + extra
    frames = proc.push(chunk, _TIMESTAMP)
    assert len(frames) == 1  # aligned portion emitted one frame
    assert proc._remainder == extra  # incomplete bytes held


def test_push_remainder_prepended_to_next_chunk() -> None:
    """Extra bytes from chunk N combine with start of chunk N+1."""
    proc = IQProcessor(make_descriptor(), make_rf_config())
    bps = SampleFormat.FLOAT32.bytes_per_sample  # 8
    extra_len = 3

    # Chunk 1: fft_size samples + 3 extra bytes
    chunk1 = make_float32_iq_bytes(_FFT_SIZE) + b"\x00" * extra_len
    proc.push(chunk1, _TIMESTAMP)
    assert len(proc._remainder) == extra_len

    # Chunk 2: just enough bytes to complete the partial sample
    completing_bytes = b"\x00" * (bps - extra_len)  # 5 bytes → 3+5=8 = 1 sample
    proc.push(completing_bytes, _TIMESTAMP)
    assert proc._remainder == b""  # no leftover after completing the sample


def test_push_all_incomplete_bytes_held_when_chunk_too_small() -> None:
    """A chunk smaller than bytes_per_sample is fully held."""
    proc = IQProcessor(make_descriptor(), make_rf_config())
    bps = SampleFormat.FLOAT32.bytes_per_sample  # 8
    tiny = b"\xab" * (bps - 1)  # 7 bytes — less than one sample
    frames = proc.push(tiny, _TIMESTAMP)
    assert frames == []
    assert proc._remainder == tiny


# ---------------------------------------------------------------------------
# push() — output frame properties
# ---------------------------------------------------------------------------


def test_push_frame_bin_count_equals_fft_size() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    frames = proc.push(make_float32_iq_bytes(_FFT_SIZE), _TIMESTAMP)
    assert frames[0].bin_count == _FFT_SIZE


def test_push_frame_payload_length_is_bin_count_times_four() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    frames = proc.push(make_float32_iq_bytes(_FFT_SIZE), _TIMESTAMP)
    f = frames[0]
    assert len(f.payload) == f.bin_count * 4


def test_push_frame_payload_is_finite_float32() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    frames = proc.push(make_float32_iq_bytes(_FFT_SIZE), _TIMESTAMP)
    values = unpack_payload(frames[0])
    assert values.dtype == np.float32
    assert np.all(np.isfinite(values))


def test_push_frame_timestamp_passed_through() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    ts = "2026-01-15T08:30:00.000Z"
    frames = proc.push(make_float32_iq_bytes(_FFT_SIZE), ts)
    assert frames[0].timestamp_utc == ts


# ---------------------------------------------------------------------------
# configure() — reconfiguration
# ---------------------------------------------------------------------------


def test_configure_changes_fft_size() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=8))
    proc.configure(make_rf_config(fft_size=32))
    # Need 32 samples for a frame now
    frames_short = proc.push(make_float32_iq_bytes(8), _TIMESTAMP)
    assert frames_short == []
    frames_full = proc.push(make_float32_iq_bytes(32 - 8), _TIMESTAMP)
    assert len(frames_full) == 1
    assert frames_full[0].bin_count == 32


def test_configure_flushes_accumulated_samples() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config(fft_size=32))
    # Accumulate some samples (not enough for a frame)
    proc.push(make_float32_iq_bytes(16), _TIMESTAMP)
    # Reconfigure — sample buffer must be discarded
    proc.configure(make_rf_config(fft_size=8))
    # With new fft_size=8, 8 samples → 1 frame (not 16+8=24)
    frames = proc.push(make_float32_iq_bytes(8), _TIMESTAMP)
    assert len(frames) == 1


def test_configure_clears_byte_remainder() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    bps = SampleFormat.FLOAT32.bytes_per_sample
    # Leave a byte remainder
    proc.push(b"\xff" * (bps - 1), _TIMESTAMP)
    assert len(proc._remainder) == bps - 1
    # Reconfigure must clear it
    proc.configure(make_rf_config())
    assert proc._remainder == b""


# ---------------------------------------------------------------------------
# run() — async integration
# ---------------------------------------------------------------------------


async def test_run_emits_frame_to_output_queue() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    iq_q: asyncio.Queue[bytes] = asyncio.Queue()
    frame_q: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    await iq_q.put(make_float32_iq_bytes(_FFT_SIZE))

    task = asyncio.create_task(proc.run(iq_q, frame_q))
    # Give the task one event-loop iteration to process the item
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert frame_q.qsize() == 1
    frame = frame_q.get_nowait()
    assert frame.bin_count == _FFT_SIZE


async def test_run_accumulates_across_multiple_queue_items() -> None:
    proc = IQProcessor(make_descriptor(), make_rf_config())
    iq_q: asyncio.Queue[bytes] = asyncio.Queue()
    frame_q: asyncio.Queue[SpectrumFrame] = asyncio.Queue()

    half = _FFT_SIZE // 2
    await iq_q.put(make_float32_iq_bytes(half))
    await iq_q.put(make_float32_iq_bytes(half))

    task = asyncio.create_task(proc.run(iq_q, frame_q))
    await asyncio.sleep(0)
    await asyncio.sleep(0)  # two items need two iterations
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert frame_q.qsize() == 1
