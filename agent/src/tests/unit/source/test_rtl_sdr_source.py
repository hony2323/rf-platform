"""Unit tests for RTLSDRSource.

All tests use a FakeRtlSdr injected via _sdr_factory — no real hardware required.
The integration-ish test at the bottom verifies that the bytes RTLSDRSource emits
parse correctly through parse_iq.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import numpy as np
import pytest

from agent.domain import Endianness, Layout, SampleFormat
from agent.processing.parse_iq import IQParseResult, parse_iq
from agent.source.rtl_sdr_source import RTLSDRSource

# ---------------------------------------------------------------------------
# Fake hardware
# ---------------------------------------------------------------------------

_CENTER_FREQ_HZ = 433_920_000
_SAMPLE_RATE_HZ = 2_048_000
_CHUNK = 64  # small so tests are fast


class FakeRtlSdr:
    """Minimal stand-in for rtlsdr.RtlSdr."""

    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        self.sample_rate: int = 0
        self.center_freq: int = 0
        self.gain: Any = "auto"
        self.closed = False
        self._call_count = 0

    def read_samples(self, n: int) -> np.ndarray:
        """Return a pure tone (unit circle) as complex64."""
        angles = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
        return (np.cos(angles) + 1j * np.sin(angles)).astype(np.complex64)

    def close(self) -> None:
        self.closed = True


def _factory(fake: FakeRtlSdr) -> Any:
    """Return an _sdr_factory callable that always hands back *fake*."""

    def _make(_device_index: int) -> FakeRtlSdr:
        fake.device_index = _device_index
        return fake

    return _make


def _make_source(
    fake: FakeRtlSdr | None = None,
    chunk: int = _CHUNK,
    gain: str | float = "auto",
    device_index: int = 0,
) -> tuple[RTLSDRSource, FakeRtlSdr]:
    hw = fake if fake is not None else FakeRtlSdr()
    src = RTLSDRSource(
        center_freq_hz=_CENTER_FREQ_HZ,
        sample_rate_hz=_SAMPLE_RATE_HZ,
        device_index=device_index,
        gain=gain,
        chunk_samples=chunk,
        _sdr_factory=_factory(hw),
    )
    return src, hw


# ---------------------------------------------------------------------------
# Descriptor tests (before start)
# ---------------------------------------------------------------------------


def test_descriptor_sample_format_is_float32() -> None:
    src, _ = _make_source()
    assert src.descriptor.sample_format == SampleFormat.FLOAT32


def test_descriptor_endianness_is_little() -> None:
    src, _ = _make_source()
    assert src.descriptor.endianness == Endianness.LITTLE


def test_descriptor_layout_is_interleaved() -> None:
    src, _ = _make_source()
    assert src.descriptor.layout == Layout.INTERLEAVED


def test_descriptor_sample_rate_matches_constructor() -> None:
    src, _ = _make_source()
    assert src.descriptor.sample_rate_hz == _SAMPLE_RATE_HZ


def test_descriptor_center_freq_matches_constructor() -> None:
    src, _ = _make_source()
    assert src.descriptor.center_freq_hz == _CENTER_FREQ_HZ


def test_descriptor_normalize_is_true() -> None:
    src, _ = _make_source()
    assert src.descriptor.normalize is True


def test_descriptor_dc_offset_remove_is_true() -> None:
    src, _ = _make_source()
    assert src.descriptor.dc_offset_remove is True


# ---------------------------------------------------------------------------
# start() — hardware configuration
# ---------------------------------------------------------------------------


async def test_start_sets_sample_rate_on_device() -> None:
    src, hw = _make_source()
    await src.start()
    assert hw.sample_rate == _SAMPLE_RATE_HZ


async def test_start_sets_center_freq_on_device() -> None:
    src, hw = _make_source()
    await src.start()
    assert hw.center_freq == _CENTER_FREQ_HZ


async def test_start_sets_gain_auto_by_default() -> None:
    src, hw = _make_source(gain="auto")
    await src.start()
    assert hw.gain == "auto"


async def test_start_sets_numeric_gain() -> None:
    src, hw = _make_source(gain=40.2)
    await src.start()
    assert hw.gain == pytest.approx(40.2)


async def test_start_passes_device_index_to_factory() -> None:
    src, hw = _make_source(device_index=2)
    await src.start()
    assert hw.device_index == 2


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


async def test_stop_closes_device() -> None:
    src, hw = _make_source()
    await src.start()
    await src.stop()
    assert hw.closed is True


async def test_stop_before_start_is_a_noop() -> None:
    src, hw = _make_source()
    await src.stop()  # must not raise
    assert hw.closed is False


# ---------------------------------------------------------------------------
# run() — output shape and type
# ---------------------------------------------------------------------------


async def _collect(src: RTLSDRSource, n: int) -> list[bytes]:
    q: asyncio.Queue[bytes] = asyncio.Queue()
    task = asyncio.create_task(src.run(q))
    blocks = []
    for _ in range(n):
        blocks.append(await asyncio.wait_for(q.get(), timeout=5.0))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return blocks


async def test_run_produces_bytes() -> None:
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    blocks = await _collect(src, n=3)
    assert all(isinstance(b, bytes) for b in blocks)
    assert all(len(b) > 0 for b in blocks)


async def test_run_block_length_is_chunk_times_8() -> None:
    """chunk_samples complex64 → chunk_samples * 2 float32 values → * 4 bytes = * 8."""
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    [block] = await _collect(src, n=1)
    assert len(block) == _CHUNK * 8  # 2 floats/sample × 4 bytes/float


async def test_run_block_aligned_to_bytes_per_sample() -> None:
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    bps = src.descriptor.bytes_per_sample
    blocks = await _collect(src, n=4)
    for block in blocks:
        assert len(block) % bps == 0


async def test_run_raises_before_start() -> None:
    src, _ = _make_source()
    q: asyncio.Queue[bytes] = asyncio.Queue()
    with pytest.raises(RuntimeError):
        await src.run(q)


async def test_run_cancels_cleanly() -> None:
    src, _ = _make_source()
    await src.start()
    q: asyncio.Queue[bytes] = asyncio.Queue()
    task = asyncio.create_task(src.run(q))
    await asyncio.sleep(0)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Integration-ish: bytes from RTLSDRSource parse through parse_iq correctly
# ---------------------------------------------------------------------------


async def test_rtlsdr_bytes_parse_through_parse_iq() -> None:
    """Bytes emitted by RTLSDRSource parse without error through parse_iq."""
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    [block] = await _collect(src, n=1)

    result = parse_iq(src.descriptor, block)
    assert isinstance(result, IQParseResult), f"parse_iq returned error: {result}"


async def test_rtlsdr_parsed_samples_are_float32() -> None:
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    [block] = await _collect(src, n=1)

    result = parse_iq(src.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32


async def test_rtlsdr_parsed_samples_within_unit_range() -> None:
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    [block] = await _collect(src, n=1)

    result = parse_iq(src.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)


async def test_rtlsdr_parsed_sample_count_matches_chunk() -> None:
    src, _ = _make_source(chunk=_CHUNK)
    await src.start()
    [block] = await _collect(src, n=1)

    result = parse_iq(src.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert result.sample_count == _CHUNK
