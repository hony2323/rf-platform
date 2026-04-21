"""WAV recording source.

Reads a .wav file and produces raw IQ byte blocks that satisfy the IQSource
protocol.

WAV encodes IQ as stereo audio: left channel = I, right channel = Q.
Because WAV carries no RF metadata, center_freq_hz must be supplied by the
caller.

Supported WAV formats:
  AudioFormat 1 (PCM)       + 8-bit  stereo → SampleFormat.UINT8
  AudioFormat 1 (PCM)       + 16-bit stereo → SampleFormat.INT16
  AudioFormat 3 (IEEE Float) + 32-bit stereo → SampleFormat.FLOAT32
  AudioFormat 3 (IEEE Float) + 64-bit stereo → SampleFormat.FLOAT64

All WAV files are little-endian (RIFF standard).
"""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.source.base import IQSource

_DEFAULT_BLOCK_BYTES = 65_536

# (audio_format, bits_per_sample) → SampleFormat
_WAV_FORMAT_MAP: dict[tuple[int, int], SampleFormat] = {
    (1, 8): SampleFormat.UINT8,
    (1, 16): SampleFormat.INT16,
    (3, 32): SampleFormat.FLOAT32,
    (3, 64): SampleFormat.FLOAT64,
}

# WAV AudioFormat codes
_WAVE_FORMAT_PCM = 1
_WAVE_FORMAT_IEEE_FLOAT = 3

# Maximum header bytes to read when locating the data chunk.
# Normal WAV headers are <100 bytes; 4 KiB covers any embedded metadata.
_HEADER_READ_SIZE = 4096


class UnsupportedWavFormatError(ValueError):
    pass


def read_iq_descriptor(wav_path: Path, center_freq_hz: int) -> IQDescriptor:
    """Synchronously read a WAV header and return an IQDescriptor."""
    with wav_path.open("rb") as f:
        header_data = f.read(_HEADER_READ_SIZE)
    sample_format, sample_rate_hz, _ = _parse_wav_header(header_data)
    return IQDescriptor(
        sample_format=sample_format,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=sample_rate_hz,
        center_freq_hz=center_freq_hz,
    )


def _parse_wav_header(data: bytes) -> tuple[SampleFormat, int, int]:
    """Parse a RIFF/WAVE header and return (sample_format, sample_rate_hz, data_offset).

    data_offset is the byte offset of the first audio sample within the file.
    Raises UnsupportedWavFormatError for invalid or unsupported files.
    """
    if len(data) < 12:
        raise UnsupportedWavFormatError("File too small to be a valid WAV")
    if data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise UnsupportedWavFormatError("Not a valid RIFF/WAVE file")

    audio_format: int | None = None
    num_channels: int | None = None
    sample_rate: int | None = None
    bits_per_sample: int | None = None
    data_offset: int | None = None

    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset : offset + 4]
        chunk_size: int = struct.unpack_from("<I", data, offset + 4)[0]
        chunk_data_start = offset + 8

        if chunk_id == b"fmt ":
            if chunk_size < 16:
                raise UnsupportedWavFormatError(
                    f"fmt chunk too small ({chunk_size} bytes)"
                )
            # fmt payload layout:
            #   0  H  AudioFormat
            #   2  H  NumChannels
            #   4  I  SampleRate
            #   8  I  ByteRate
            #  12  H  BlockAlign
            #  14  H  BitsPerSample
            audio_format, num_channels, sample_rate = struct.unpack_from(
                "<HHI", data, chunk_data_start
            )
            (bits_per_sample,) = struct.unpack_from("<H", data, chunk_data_start + 14)

        elif chunk_id == b"data":
            data_offset = chunk_data_start
            break

        # Chunks are word-aligned; odd-size chunks have a 1-byte pad.
        offset += 8 + chunk_size + (chunk_size % 2)

    if audio_format is None or num_channels is None or sample_rate is None:
        raise UnsupportedWavFormatError("WAV file is missing the fmt chunk")
    if bits_per_sample is None:
        raise UnsupportedWavFormatError("WAV fmt chunk is missing bits_per_sample")
    if data_offset is None:
        raise UnsupportedWavFormatError("WAV file is missing the data chunk")
    if num_channels != 2:
        raise UnsupportedWavFormatError(
            f"WAV IQ source requires stereo (2 channels), got {num_channels}"
        )

    key = (audio_format, bits_per_sample)
    if key not in _WAV_FORMAT_MAP:
        supported = sorted(_WAV_FORMAT_MAP)
        raise UnsupportedWavFormatError(
            f"Unsupported WAV format: AudioFormat={audio_format}, "
            f"BitsPerSample={bits_per_sample}. "
            f"Supported (audio_format, bits_per_sample): {supported}"
        )

    return _WAV_FORMAT_MAP[key], sample_rate, data_offset


class WavSource(IQSource):
    """IQSource backed by a WAV recording.

    WAV encodes IQ as stereo audio: left channel = I, right channel = Q.
    Because WAV carries no RF metadata, center_freq_hz must be supplied.

    Args:
        wav_path:       Path to the .wav file.
        center_freq_hz: RF center frequency. Not stored in WAV; caller must
                        supply it to match the stream_config.
        block_size:     Approximate read size in bytes. Rounded down to the
                        nearest sample boundary before use.
        loops:          Number of times to play the recording. 1 = play once
                        (default, no looping). None = loop forever.
    """

    def __init__(
        self,
        wav_path: Path,
        center_freq_hz: int,
        block_size: int = _DEFAULT_BLOCK_BYTES,
        loops: int | None = 1,
        rate_limit_msps: float | None = None,
    ) -> None:
        self._wav_path = wav_path
        self._center_freq_hz = center_freq_hz
        self._block_size = block_size
        self._loops = loops
        self._rate_limit_msps = rate_limit_msps
        self._descriptor: IQDescriptor | None = None
        self._data_offset: int | None = None

    @property
    def descriptor(self) -> IQDescriptor:
        if self._descriptor is None:
            raise RuntimeError("call start() before accessing descriptor")
        return self._descriptor

    async def start(self) -> None:
        """Parse the WAV header and build the IQDescriptor."""
        with self._wav_path.open("rb") as f:
            header_data = f.read(_HEADER_READ_SIZE)

        sample_format, sample_rate_hz, data_offset = _parse_wav_header(header_data)
        self._data_offset = data_offset
        self._descriptor = IQDescriptor(
            sample_format=sample_format,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=sample_rate_hz,
            center_freq_hz=self._center_freq_hz,
        )

    async def stop(self) -> None:
        pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        """Read the WAV data section and push aligned byte blocks to output.

        Trims any trailing bytes that would form an incomplete sample.
        Raises asyncio.CancelledError on cancellation.
        """
        if self._descriptor is None or self._data_offset is None:
            raise RuntimeError("call start() before run()")

        bps = self._descriptor.bytes_per_sample
        block_size = (self._block_size // bps) * bps
        if block_size == 0:
            raise ValueError(
                f"block_size {self._block_size} is smaller than bytes_per_sample {bps}"
            )

        # Rate limiting: timestamp-based leaky-bucket so the output rate matches
        # the target MSPS even on platforms with coarse sleep granularity.
        # We track total samples sent and compute a cumulative "expected time"
        # for each block. When we're ahead of schedule we sleep the deficit;
        # when we're behind (e.g. previous sleep overshot) we skip sleeping and
        # let the pipeline drain first, self-correcting without drift.
        rate_limit_sps: float | None = (
            self._rate_limit_msps * 1e6 if self._rate_limit_msps is not None else None
        )
        start_time: float | None = None
        total_samples: int = 0

        iteration = 0
        while self._loops is None or iteration < self._loops:
            with self._wav_path.open("rb") as f:
                f.seek(self._data_offset)
                while True:
                    chunk = f.read(block_size)
                    if not chunk:
                        break
                    # Trim trailing partial sample (shouldn't happen for
                    # well-formed files, but be defensive)
                    remainder = len(chunk) % bps
                    if remainder:
                        chunk = chunk[:-remainder]
                    if chunk:
                        if rate_limit_sps is not None:
                            if start_time is None:
                                start_time = asyncio.get_event_loop().time()
                                total_samples = 0
                            samples_in_block = len(chunk) // bps
                            total_samples += samples_in_block
                            expected_t = start_time + total_samples / rate_limit_sps
                            now = asyncio.get_event_loop().time()
                            gap = expected_t - now
                            if gap > 0:
                                await asyncio.sleep(gap)
                        await output.put(chunk)
            iteration += 1
