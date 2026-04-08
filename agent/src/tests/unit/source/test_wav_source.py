"""Unit tests for WavSource.

Synthetic WAV fixtures are generated in-memory using tmp_path — no committed
binary files required for the format / error tests.

Real-fixture tests at the bottom use the committed MWlamp WAV fixture via the
wav_mwlamp_path / wav_mwlamp_source fixtures defined in conftest.py.
"""

from __future__ import annotations

import asyncio
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from agent.domain import Endianness, Layout, SampleFormat
from agent.processing.parse_iq import IQParseResult, parse_iq
from agent.source.wav import UnsupportedWavFormatError, WavSource
from tests.conftest import (
    WAV_AUDIO_CENTER_FREQ_HZ,
    WAV_AUDIO_SAMPLE_RATE_HZ,
    WAV_MWLAMP_CENTER_FREQ_HZ,
    WAV_MWLAMP_SAMPLE_RATE_HZ,
)

# ---------------------------------------------------------------------------
# WAV fixture helpers
# ---------------------------------------------------------------------------

_CENTER_FREQ_HZ = 433_920_000
_SAMPLE_RATE = 2_400_000
_N_SAMPLES = 1024  # complex samples (I+Q pairs)


def _write_pcm16_wav(path: Path, sample_rate: int, n_samples: int) -> None:
    """Write a stereo 16-bit PCM WAV file with a simple ramp pattern."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        # Each frame: I sample (int16) + Q sample (int16)
        samples = [i % 1000 for i in range(n_samples * 2)]
        payload = struct.pack(f"<{n_samples * 2}h", *samples)
        wf.writeframes(payload)


def _write_pcm8_wav(path: Path, sample_rate: int, n_samples: int) -> None:
    """Write a stereo 8-bit PCM WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(1)  # 8-bit
        wf.setframerate(sample_rate)
        samples = bytes([i % 256 for i in range(n_samples * 2)])
        wf.writeframes(samples)


def _write_float32_wav(path: Path, sample_rate: int, n_samples: int) -> None:
    """Write a stereo 32-bit IEEE float WAV file."""
    # RIFF header: RIFF + file_size + WAVE
    # fmt chunk:   "fmt " + 16 + AudioFormat=3 + NumChannels=2 + SampleRate
    #              + ByteRate + BlockAlign + BitsPerSample
    # data chunk:  "data" + data_size + payload
    num_channels = 2
    bits_per_sample = 32
    block_align = num_channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    fmt = struct.pack(
        "<HHIIHH",
        3,  # AudioFormat: IEEE float
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    payload = struct.pack(f"<{n_samples * 2}f", *([0.5] * (n_samples * 2)))
    data_chunk = b"data" + struct.pack("<I", len(payload)) + payload
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    wave_body = fmt_chunk + data_chunk
    riff = b"RIFF" + struct.pack("<I", 4 + len(wave_body)) + b"WAVE" + wave_body
    path.write_bytes(riff)


def _write_float64_wav(path: Path, sample_rate: int, n_samples: int) -> None:
    """Write a stereo 64-bit IEEE float WAV file."""
    num_channels = 2
    bits_per_sample = 64
    block_align = num_channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    fmt = struct.pack(
        "<HHIIHH",
        3,  # AudioFormat: IEEE float
        num_channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
    )
    payload = struct.pack(f"<{n_samples * 2}d", *([0.25] * (n_samples * 2)))
    data_chunk = b"data" + struct.pack("<I", len(payload)) + payload
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    wave_body = fmt_chunk + data_chunk
    riff = b"RIFF" + struct.pack("<I", 4 + len(wave_body)) + b"WAVE" + wave_body
    path.write_bytes(riff)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pcm16_wav(tmp_path: Path) -> Path:
    p = tmp_path / "iq.wav"
    _write_pcm16_wav(p, _SAMPLE_RATE, _N_SAMPLES)
    return p


@pytest.fixture
def pcm8_wav(tmp_path: Path) -> Path:
    p = tmp_path / "iq8.wav"
    _write_pcm8_wav(p, _SAMPLE_RATE, _N_SAMPLES)
    return p


@pytest.fixture
def float32_wav(tmp_path: Path) -> Path:
    p = tmp_path / "iq_f32.wav"
    _write_float32_wav(p, _SAMPLE_RATE, _N_SAMPLES)
    return p


@pytest.fixture
def float64_wav(tmp_path: Path) -> Path:
    p = tmp_path / "iq_f64.wav"
    _write_float64_wav(p, _SAMPLE_RATE, _N_SAMPLES)
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_blocks(source: WavSource, n: int) -> list[bytes]:
    q: asyncio.Queue[bytes] = asyncio.Queue()
    task = asyncio.create_task(source.run(q))
    blocks = []
    for _ in range(n):
        blocks.append(await asyncio.wait_for(q.get(), timeout=5.0))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return blocks


# ---------------------------------------------------------------------------
# Descriptor tests — 16-bit PCM (most common IQ WAV format)
# ---------------------------------------------------------------------------


async def test_wav_source_descriptor_sample_format_pcm16(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.sample_format == SampleFormat.INT16


async def test_wav_source_descriptor_endianness_is_always_little(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.endianness == Endianness.LITTLE


async def test_wav_source_descriptor_layout_is_interleaved(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.layout == Layout.INTERLEAVED


async def test_wav_source_descriptor_sample_rate_from_header(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.sample_rate_hz == _SAMPLE_RATE


async def test_wav_source_descriptor_center_freq_from_constructor(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.center_freq_hz == _CENTER_FREQ_HZ


async def test_wav_source_descriptor_raises_before_start(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    with pytest.raises(RuntimeError):
        _ = source.descriptor


# ---------------------------------------------------------------------------
# Descriptor tests — other supported formats
# ---------------------------------------------------------------------------


async def test_wav_source_descriptor_sample_format_pcm8(pcm8_wav: Path) -> None:
    source = WavSource(pcm8_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.sample_format == SampleFormat.UINT8


async def test_wav_source_descriptor_sample_format_float32(
    float32_wav: Path,
) -> None:
    source = WavSource(float32_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.sample_format == SampleFormat.FLOAT32


async def test_wav_source_descriptor_sample_format_float64(
    float64_wav: Path,
) -> None:
    source = WavSource(float64_wav, center_freq_hz=_CENTER_FREQ_HZ)
    await source.start()
    assert source.descriptor.sample_format == SampleFormat.FLOAT64


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_wav_source_start_raises_on_not_a_wav(tmp_path: Path) -> None:
    bad = tmp_path / "not.wav"
    bad.write_bytes(b"this is not a wav file at all")
    source = WavSource(bad, center_freq_hz=_CENTER_FREQ_HZ)
    with pytest.raises(UnsupportedWavFormatError):
        await source.start()


async def test_wav_source_start_raises_on_mono(tmp_path: Path) -> None:
    p = tmp_path / "mono.wav"
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(struct.pack("<100h", *([0] * 100)))
    source = WavSource(p, center_freq_hz=_CENTER_FREQ_HZ)
    with pytest.raises(UnsupportedWavFormatError, match="stereo"):
        await source.start()


async def test_wav_source_start_raises_on_unsupported_bit_depth(
    tmp_path: Path,
) -> None:
    # 24-bit PCM stereo — not in _WAV_FORMAT_MAP
    p = tmp_path / "pcm24.wav"
    num_channels = 2
    bits_per_sample = 24
    sample_rate = _SAMPLE_RATE
    block_align = num_channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    fmt = struct.pack(
        "<HHIIHH", 1, num_channels, sample_rate, byte_rate, block_align, bits_per_sample
    )
    payload = bytes(block_align * 10)
    data_chunk = b"data" + struct.pack("<I", len(payload)) + payload
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    wave_body = fmt_chunk + data_chunk
    riff = b"RIFF" + struct.pack("<I", 4 + len(wave_body)) + b"WAVE" + wave_body
    p.write_bytes(riff)
    source = WavSource(p, center_freq_hz=_CENTER_FREQ_HZ)
    with pytest.raises(UnsupportedWavFormatError):
        await source.start()


async def test_wav_source_run_raises_before_start(pcm16_wav: Path) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ)
    q: asyncio.Queue[bytes] = asyncio.Queue()
    with pytest.raises(RuntimeError):
        await source.run(q)


# ---------------------------------------------------------------------------
# run() tests
# ---------------------------------------------------------------------------


async def test_wav_source_run_produces_bytes(pcm16_wav: Path) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=512)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    assert all(isinstance(b, bytes) for b in blocks)
    assert all(len(b) > 0 for b in blocks)


async def test_wav_source_run_blocks_aligned_to_bytes_per_sample(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=512)
    await source.start()
    bps = source.descriptor.bytes_per_sample
    blocks = await _collect_blocks(source, n=5)
    for block in blocks:
        assert len(block) % bps == 0, (
            f"block length {len(block)} is not a multiple of bytes_per_sample {bps}"
        )


async def test_wav_source_run_block_size_respected_approximately(
    pcm16_wav: Path,
) -> None:
    block_size = 512
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=block_size)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    for block in blocks:
        assert len(block) <= block_size


async def test_wav_source_run_blocks_parse_without_error(pcm16_wav: Path) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=512)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    for block in blocks:
        result = parse_iq(source.descriptor, block)
        assert isinstance(result, IQParseResult), f"parse_iq failed: {result}"


async def test_wav_source_run_parsed_samples_are_float32(pcm16_wav: Path) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=512)
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32


async def test_wav_source_run_parsed_samples_normalized_to_unit_range(
    pcm16_wav: Path,
) -> None:
    source = WavSource(pcm16_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=512)
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)


async def test_wav_source_run_loops(tmp_path: Path) -> None:
    """Two loops should produce more blocks than the file holds in one pass."""
    p = tmp_path / "iq.wav"
    # Write a tiny file: exactly 4 samples → 1 block at block_size=16 (4 bytes/sample)
    with wave.open(str(p), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(struct.pack("<8h", *([100] * 8)))  # 4 complex samples

    source = WavSource(p, center_freq_hz=_CENTER_FREQ_HZ, block_size=16, loops=2)
    await source.start()

    q: asyncio.Queue[bytes] = asyncio.Queue()
    await source.run(q)  # runs to completion (2 loops, then exits)

    blocks = []
    while not q.empty():
        blocks.append(q.get_nowait())

    # 2 loops × 1 block each = 2 blocks
    assert len(blocks) == 2


async def test_wav_source_run_float32_blocks_parse_without_error(
    float32_wav: Path,
) -> None:
    source = WavSource(float32_wav, center_freq_hz=_CENTER_FREQ_HZ, block_size=512)
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    for block in blocks:
        result = parse_iq(source.descriptor, block)
        assert isinstance(result, IQParseResult), f"parse_iq failed: {result}"


# ---------------------------------------------------------------------------
# Real-fixture tests — MWlamp WAV (PCM uint8, 5829.25 MHz, 1.25 Msps)
# ---------------------------------------------------------------------------


async def test_mwlamp_descriptor_sample_format_is_uint8(
    wav_mwlamp_source: WavSource,
) -> None:
    assert wav_mwlamp_source.descriptor.sample_format == SampleFormat.UINT8


async def test_mwlamp_descriptor_endianness_is_little(
    wav_mwlamp_source: WavSource,
) -> None:
    assert wav_mwlamp_source.descriptor.endianness == Endianness.LITTLE


async def test_mwlamp_descriptor_layout_is_interleaved(
    wav_mwlamp_source: WavSource,
) -> None:
    assert wav_mwlamp_source.descriptor.layout == Layout.INTERLEAVED


async def test_mwlamp_descriptor_sample_rate(wav_mwlamp_source: WavSource) -> None:
    assert wav_mwlamp_source.descriptor.sample_rate_hz == WAV_MWLAMP_SAMPLE_RATE_HZ


async def test_mwlamp_descriptor_center_freq(wav_mwlamp_source: WavSource) -> None:
    assert wav_mwlamp_source.descriptor.center_freq_hz == WAV_MWLAMP_CENTER_FREQ_HZ


async def test_mwlamp_run_produces_bytes(wav_mwlamp_path: Path) -> None:
    source = WavSource(
        wav_mwlamp_path,
        center_freq_hz=WAV_MWLAMP_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    assert all(isinstance(b, bytes) for b in blocks)
    assert all(len(b) > 0 for b in blocks)


async def test_mwlamp_run_blocks_aligned_to_bytes_per_sample(
    wav_mwlamp_path: Path,
) -> None:
    source = WavSource(
        wav_mwlamp_path,
        center_freq_hz=WAV_MWLAMP_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    bps = source.descriptor.bytes_per_sample
    blocks = await _collect_blocks(source, n=5)
    for block in blocks:
        assert len(block) % bps == 0, (
            f"block length {len(block)} not a multiple of bytes_per_sample {bps}"
        )


async def test_mwlamp_run_blocks_parse_without_error(wav_mwlamp_path: Path) -> None:
    source = WavSource(
        wav_mwlamp_path,
        center_freq_hz=WAV_MWLAMP_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    blocks = await _collect_blocks(source, n=5)
    for block in blocks:
        result = parse_iq(source.descriptor, block)
        assert isinstance(result, IQParseResult), f"parse_iq failed: {result}"


async def test_mwlamp_run_parsed_samples_are_float32(wav_mwlamp_path: Path) -> None:
    source = WavSource(
        wav_mwlamp_path,
        center_freq_hz=WAV_MWLAMP_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32


async def test_mwlamp_run_parsed_samples_normalized_to_unit_range(
    wav_mwlamp_path: Path,
) -> None:
    source = WavSource(
        wav_mwlamp_path,
        center_freq_hz=WAV_MWLAMP_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)


# ---------------------------------------------------------------------------
# Real-fixture tests — audio WAV (PCM int16, 16.035 MHz, 96 ksps)
# ---------------------------------------------------------------------------


async def test_audio_descriptor_sample_format_is_int16(
    wav_audio_source: WavSource,
) -> None:
    assert wav_audio_source.descriptor.sample_format == SampleFormat.INT16


async def test_audio_descriptor_endianness_is_little(
    wav_audio_source: WavSource,
) -> None:
    assert wav_audio_source.descriptor.endianness == Endianness.LITTLE


async def test_audio_descriptor_layout_is_interleaved(
    wav_audio_source: WavSource,
) -> None:
    assert wav_audio_source.descriptor.layout == Layout.INTERLEAVED


async def test_audio_descriptor_sample_rate(wav_audio_source: WavSource) -> None:
    assert wav_audio_source.descriptor.sample_rate_hz == WAV_AUDIO_SAMPLE_RATE_HZ


async def test_audio_descriptor_center_freq(wav_audio_source: WavSource) -> None:
    assert wav_audio_source.descriptor.center_freq_hz == WAV_AUDIO_CENTER_FREQ_HZ


async def test_audio_run_produces_bytes(wav_audio_path: Path) -> None:
    source = WavSource(
        wav_audio_path,
        center_freq_hz=WAV_AUDIO_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    blocks = await _collect_blocks(source, n=3)
    assert all(isinstance(b, bytes) for b in blocks)
    assert all(len(b) > 0 for b in blocks)


async def test_audio_run_blocks_aligned_to_bytes_per_sample(
    wav_audio_path: Path,
) -> None:
    source = WavSource(
        wav_audio_path,
        center_freq_hz=WAV_AUDIO_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    bps = source.descriptor.bytes_per_sample
    blocks = await _collect_blocks(source, n=5)
    for block in blocks:
        assert len(block) % bps == 0, (
            f"block length {len(block)} not a multiple of bytes_per_sample {bps}"
        )


async def test_audio_run_blocks_parse_without_error(wav_audio_path: Path) -> None:
    source = WavSource(
        wav_audio_path,
        center_freq_hz=WAV_AUDIO_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    blocks = await _collect_blocks(source, n=5)
    for block in blocks:
        result = parse_iq(source.descriptor, block)
        assert isinstance(result, IQParseResult), f"parse_iq failed: {result}"


async def test_audio_run_parsed_samples_are_float32(wav_audio_path: Path) -> None:
    source = WavSource(
        wav_audio_path,
        center_freq_hz=WAV_AUDIO_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert result.samples.dtype == np.float32


async def test_audio_run_parsed_samples_normalized_to_unit_range(
    wav_audio_path: Path,
) -> None:
    source = WavSource(
        wav_audio_path,
        center_freq_hz=WAV_AUDIO_CENTER_FREQ_HZ,
        block_size=4096,
    )
    await source.start()
    [block] = await _collect_blocks(source, n=1)
    result = parse_iq(source.descriptor, block)
    assert isinstance(result, IQParseResult)
    assert np.all(result.samples >= -1.0)
    assert np.all(result.samples <= 1.0)
