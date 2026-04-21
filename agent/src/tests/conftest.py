"""Shared pytest fixtures for all agent tests."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.source.sigmf import SigMFSource
from agent.source.wav import WavSource

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_LTE_DIR = _FIXTURES_DIR / "LTE_uplink_847MHz_2022-01-30_30720ksps_fix"
_MWLAMP_DIR = _FIXTURES_DIR / "MWlamp_5829250000Hz_1250ksps_fix"
_AUDIO_DIR = _FIXTURES_DIR / "audio_16035000Hz_96ksps_fix"

# Known constants for the MWlamp WAV fixture (decoded from filename + header).
WAV_MWLAMP_CENTER_FREQ_HZ = 5_829_250_000
WAV_MWLAMP_SAMPLE_RATE_HZ = 1_250_000

# Known constants for the audio WAV fixture (decoded from filename + header).
WAV_AUDIO_CENTER_FREQ_HZ = 16_035_000
WAV_AUDIO_SAMPLE_RATE_HZ = 96_000


@pytest.fixture
def sigmf_lte_meta_path() -> Path:
    """Path to the trimmed LTE uplink SigMF fixture (ci16_le, 847 MHz, 30.72 Msps).

    This is a reduced-size recording suitable for CI. Full-length recordings
    belong in recordings/ at the repo root (gitignored).
    """
    return _LTE_DIR / "LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-meta"


class SigMFBuffer(NamedTuple):
    descriptor: IQDescriptor
    raw_bytes: bytes


@pytest.fixture
async def sigmf_lte_buffer(sigmf_lte_meta_path: Path) -> SigMFBuffer:
    """Descriptor and raw bytes from the LTE SigMF fixture, ready for parse_iq.

    Uses SigMFSource to build the descriptor — intended for SigMFSource tests.
    Parser tests should use lte_ci16_raw instead.
    """
    source = SigMFSource(sigmf_lte_meta_path)
    await source.start()
    raw_bytes = sigmf_lte_meta_path.with_suffix(".sigmf-data").read_bytes()
    return SigMFBuffer(descriptor=source.descriptor, raw_bytes=raw_bytes)


@pytest.fixture
def lte_ci16_raw() -> SigMFBuffer:
    """Raw IQ bytes from the LTE fixture with a hardcoded descriptor.

    No dependency on SigMFSource — the descriptor values are derived from the
    known sigmf-meta and fixed here. Use this in parser unit tests so they
    remain independent of the source layer.
    """
    descriptor = IQDescriptor(
        sample_format=SampleFormat.INT16,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=30_720_000,
        center_freq_hz=847_000_000,
    )
    data_file = _LTE_DIR / "LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-data"
    raw_bytes = data_file.read_bytes()
    return SigMFBuffer(descriptor=descriptor, raw_bytes=raw_bytes)


@pytest.fixture
def wav_mwlamp_path() -> Path:
    """Path to the MWlamp WAV fixture (PCM uint8, 5829.25 MHz, 1.25 Msps).

    8-bit stereo PCM, 100 k complex samples. Trimmed for CI.
    Center frequency decoded from the filename; not stored in the WAV header.
    """
    return _MWLAMP_DIR / "5829250000Hz_MWlamp_1250k_small.wav"


@pytest.fixture
async def wav_mwlamp_source(wav_mwlamp_path: Path) -> WavSource:
    """Started WavSource for the MWlamp fixture, ready for descriptor access."""
    source = WavSource(wav_mwlamp_path, center_freq_hz=WAV_MWLAMP_CENTER_FREQ_HZ)
    await source.start()
    return source


@pytest.fixture
def wav_audio_path() -> Path:
    """Path to the audio WAV fixture (PCM int16, 16.035 MHz, 96 ksps).

    16-bit stereo PCM, 100 k complex samples. Trimmed for CI.
    Center frequency decoded from the filename; not stored in the WAV header.
    """
    return _AUDIO_DIR / "audio_16035000Hz_96k_small.wav"


@pytest.fixture
async def wav_audio_source(wav_audio_path: Path) -> WavSource:
    """Started WavSource for the audio fixture, ready for descriptor access."""
    source = WavSource(wav_audio_path, center_freq_hz=WAV_AUDIO_CENTER_FREQ_HZ)
    await source.start()
    return source
