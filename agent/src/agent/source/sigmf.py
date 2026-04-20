"""SigMF recording source.

Reads a .sigmf-meta / .sigmf-data pair and produces raw IQ byte blocks
that satisfy the IQSource protocol.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat
from agent.source.base import IQSource

# Maps SigMF core:datatype strings to (SampleFormat, Endianness).
# Only complex interleaved types are supported (MVP: interleaved layout only).
_DATATYPE_MAP: dict[str, tuple[SampleFormat, Endianness]] = {
    "ci16_le": (SampleFormat.INT16, Endianness.LITTLE),
    "ci16_be": (SampleFormat.INT16, Endianness.BIG),
    "cf32_le": (SampleFormat.FLOAT32, Endianness.LITTLE),
    "cf32_be": (SampleFormat.FLOAT32, Endianness.BIG),
    "cf64_le": (SampleFormat.FLOAT64, Endianness.LITTLE),
    "cf64_be": (SampleFormat.FLOAT64, Endianness.BIG),
    "cu8_le": (SampleFormat.UINT8, Endianness.LITTLE),
    # endianness irrelevant for uint8
    "cu8_be": (SampleFormat.UINT8, Endianness.LITTLE),
}

_DEFAULT_BLOCK_BYTES = 65_536


class UnsupportedSigMFDatatypeError(ValueError):
    pass


class SigMFSource(IQSource):
    """IQSource backed by a SigMF recording.

    Args:
        meta_path:       Path to the .sigmf-meta file. The .sigmf-data file is
                         expected alongside it with the same stem.
        block_size:      Approximate read size in bytes. Rounded down to the
                         nearest sample boundary before use.
        loops:           Number of times to play the recording. 1 = play once
                         (default, no looping). None = loop forever.
        rate_limit_msps: Throttle output to N million samples/sec. None = unlimited.
    """

    def __init__(
        self,
        meta_path: Path,
        block_size: int = _DEFAULT_BLOCK_BYTES,
        loops: int | None = 1,
        rate_limit_msps: float | None = None,
    ) -> None:
        self._meta_path = meta_path
        self._data_path = meta_path.with_suffix(".sigmf-data")
        self._block_size = block_size
        self._loops = loops
        self._rate_limit_msps = rate_limit_msps
        self._descriptor: IQDescriptor | None = None

    @property
    def descriptor(self) -> IQDescriptor:
        if self._descriptor is None:
            raise RuntimeError("call start() before accessing descriptor")
        return self._descriptor

    async def start(self) -> None:
        """Parse the .sigmf-meta file and build the IQDescriptor."""
        meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
        g = meta["global"]

        datatype: str = g["core:datatype"]
        if datatype not in _DATATYPE_MAP:
            raise UnsupportedSigMFDatatypeError(
                f"datatype {datatype!r} is not supported. "
                f"Supported: {sorted(_DATATYPE_MAP)}"
            )

        sample_format, endianness = _DATATYPE_MAP[datatype]

        captures = meta.get("captures", [])
        if not captures:
            raise ValueError("sigmf-meta has no captures entries")

        self._descriptor = IQDescriptor(
            sample_format=sample_format,
            endianness=endianness,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=int(g["core:sample_rate"]),
            center_freq_hz=int(captures[0]["core:frequency"]),
        )

    async def stop(self) -> None:
        pass

    async def run(self, output: asyncio.Queue[bytes]) -> None:
        """Read the .sigmf-data file and push aligned byte blocks to output.

        Trims any trailing bytes that would form an incomplete sample.
        Raises asyncio.CancelledError on cancellation.
        """
        if self._descriptor is None:
            raise RuntimeError("call start() before run()")

        bps = self._descriptor.bytes_per_sample
        # Align block size to a whole-sample boundary
        block_size = (self._block_size // bps) * bps
        if block_size == 0:
            raise ValueError(
                f"block_size {self._block_size} is smaller than bytes_per_sample {bps}"
            )

        rate_limit_sps: float | None = (
            self._rate_limit_msps * 1e6 if self._rate_limit_msps is not None else None
        )
        start_time: float | None = None
        total_samples: int = 0

        iteration = 0
        while self._loops is None or iteration < self._loops:
            with self._data_path.open("rb") as f:
                while True:
                    chunk = f.read(block_size)
                    if not chunk:
                        break
                    # Trim trailing partial sample (shouldn't happen for well-formed
                    # files, but be defensive)
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
