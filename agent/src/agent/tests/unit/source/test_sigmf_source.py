"""Unit tests for SigMFSource.

Uses the trimmed LTE uplink SigMF fixture (ci16_le, 30.72 MHz, 847 MHz).
run() tests cancel after receiving a few blocks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from agent.domain import Endianness, Layout, SampleFormat
from agent.processing.parse_iq import IQParseResult, parse_iq
from agent.source.sigmf import SigMFSource, UnsupportedSigMFDatatype


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_blocks(source: SigMFSource, n: int) -> list[bytes]:
    """Run source and collect exactly n blocks, then cancel."""
    q: asyncio.Queue[bytes] = asyncio.Queue()
    task = asyncio.create_task(source.run(q))
    blocks = []
    for _ in range(n):
        blocks.append(await asyncio.wait_for(q.get(), timeout=5.0))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return blocks


# ---------------------------------------------------------------------------
# Descriptor tests (fast — only reads the small meta file)
# ---------------------------------------------------------------------------


async def test_sigmf_source_descriptor_sample_format_matches_ci16_le(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path)
    await source.start()
    assert source.descriptor.sample_format == SampleFormat.INT16


async def test_sigmf_source_descriptor_endianness_is_little(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path)
    await source.start()
    assert source.descriptor.endianness == Endianness.LITTLE


async def test_sigmf_source_descriptor_layout_is_interleaved(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path)
    await source.start()
    assert source.descriptor.layout == Layout.INTERLEAVED


async def test_sigmf_source_descriptor_sample_rate_matches_meta(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path)
    await source.start()
    assert source.descriptor.sample_rate_hz == 30_720_000


async def test_sigmf_source_descriptor_center_freq_matches_capture(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path)
    await source.start()
    assert source.descriptor.center_freq_hz == 847_000_000


async def test_sigmf_source_descriptor_raises_before_start(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path)
    with pytest.raises(RuntimeError):
        _ = source.descriptor


async def test_sigmf_source_start_raises_on_unsupported_datatype(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "test.sigmf-meta"
    meta.write_text(
        '{"global": {"core:datatype": "ri8_le", "core:sample_rate": 1000000},'
        ' "captures": [{"core:frequency": 100000000, "core:sample_start": 0}],'
        ' "annotations": []}',
        encoding="utf-8",
    )
    source = SigMFSource(meta)
    with pytest.raises(UnsupportedSigMFDatatype):
        await source.start()


# ---------------------------------------------------------------------------
# run() tests
# ---------------------------------------------------------------------------


async def test_sigmf_source_run_produces_bytes(sigmf_lte_meta_path: Path) -> None:
    source = SigMFSource(sigmf_lte_meta_path, block_size=4096)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    assert all(isinstance(b, bytes) for b in blocks)
    assert all(len(b) > 0 for b in blocks)


async def test_sigmf_source_run_blocks_are_aligned_to_bytes_per_sample(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path, block_size=4096)
    await source.start()
    bps = source.descriptor.bytes_per_sample
    blocks = await _collect_blocks(source, n=5)
    for block in blocks:
        assert len(block) % bps == 0, (
            f"block length {len(block)} is not a multiple of bytes_per_sample {bps}"
        )


async def test_sigmf_source_run_block_size_respected_approximately(
    sigmf_lte_meta_path: Path,
) -> None:
    block_size = 8192
    source = SigMFSource(sigmf_lte_meta_path, block_size=block_size)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    for block in blocks:
        assert len(block) <= block_size


async def test_sigmf_source_run_blocks_parse_without_error(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path, block_size=4096)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    for block in blocks:
        result = parse_iq(source.descriptor, block)
        assert isinstance(result, IQParseResult), f"parse_iq failed: {result}"


async def test_sigmf_source_run_parsed_samples_are_float32(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path, block_size=4096)
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32


async def test_sigmf_source_run_parsed_samples_normalized_to_unit_range(
    sigmf_lte_meta_path: Path,
) -> None:
    source = SigMFSource(sigmf_lte_meta_path, block_size=4096)
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)
