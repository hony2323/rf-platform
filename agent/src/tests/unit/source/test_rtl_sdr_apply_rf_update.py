"""Unit tests for RTLSDRSource.apply_rf_update (live retune)."""

from __future__ import annotations

import asyncio
import math
import threading
from typing import Any

import numpy as np

from agent.domain import RFConfig, TunerConfig
from agent.source.base import LiveRetunableSource
from agent.source.rtl_sdr_source import RTLSDRSource


class FakeRtlSdr:
    def __init__(self, device_index: int = 0) -> None:
        self.device_index = device_index
        self.sample_rate: int = 0
        self.center_freq: int = 0
        self.gain: Any = "auto"
        self.closed = False

    def read_samples(self, n: int) -> np.ndarray:
        angles = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
        return (np.cos(angles) + 1j * np.sin(angles)).astype(np.complex64)

    def close(self) -> None:
        self.closed = True


def _make_source(fake: FakeRtlSdr) -> RTLSDRSource:
    return RTLSDRSource(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_048_000,
        gain="auto",
        chunk_samples=64,
        _sdr_factory=lambda _idx: fake,
    )


def test_rtl_sdr_advertises_live_retune_capability() -> None:
    src = _make_source(FakeRtlSdr())
    assert isinstance(src, LiveRetunableSource)


async def test_apply_rf_update_updates_hardware_fields() -> None:
    fake = FakeRtlSdr()
    src = _make_source(fake)
    await src.start()

    await src.apply_rf_update(
        RFConfig(
            center_freq_hz=100_000_000,
            sample_rate_hz=1_024_000,
            fft_size=8192,
        ),
        tuner=TunerConfig(gain_db=30.5, agc=False),
    )

    assert fake.center_freq == 100_000_000
    assert fake.sample_rate == 1_024_000
    assert fake.gain == 30.5
    assert src.descriptor.center_freq_hz == 100_000_000
    assert src.descriptor.sample_rate_hz == 1_024_000

    await src.stop()


async def test_apply_rf_update_with_agc_sets_gain_auto() -> None:
    fake = FakeRtlSdr()
    src = _make_source(fake)
    await src.start()

    await src.apply_rf_update(
        RFConfig(center_freq_hz=100_000_000, sample_rate_hz=2_400_000, fft_size=4096),
        tuner=TunerConfig(gain_db=None, agc=True),
    )
    assert fake.gain == "auto"

    await src.stop()


async def test_apply_rf_update_omitted_tuner_leaves_gain_unchanged() -> None:
    fake = FakeRtlSdr()
    fake_gain_before = 25.4
    src = _make_source(fake)
    await src.start()
    fake.gain = fake_gain_before  # reset to a known state post-start

    await src.apply_rf_update(
        RFConfig(center_freq_hz=200_000_000, sample_rate_hz=2_400_000, fft_size=4096),
        tuner=None,
    )
    assert fake.gain == fake_gain_before
    assert fake.center_freq == 200_000_000
    await src.stop()


async def test_apply_rf_update_bumps_generation_and_descriptor() -> None:
    fake = FakeRtlSdr()
    src = _make_source(fake)
    await src.start()
    before = src._retune_generation  # type: ignore[attr-defined]
    await src.apply_rf_update(
        RFConfig(center_freq_hz=150_000_000, sample_rate_hz=2_048_000, fft_size=2048),
        tuner=None,
    )
    assert src._retune_generation == before + 1  # type: ignore[attr-defined]
    assert src.descriptor.center_freq_hz == 150_000_000
    await src.stop()


async def test_apply_rf_update_before_start_raises() -> None:
    fake = FakeRtlSdr()
    src = _make_source(fake)
    try:
        await src.apply_rf_update(
            RFConfig(
                center_freq_hz=100_000_000, sample_rate_hz=1_024_000, fft_size=512
            ),
            tuner=None,
        )
    except RuntimeError as exc:
        assert "start" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


async def test_in_flight_chunk_dropped_across_retune() -> None:
    """If a retune occurs while a read_samples is in flight, that chunk is
    discarded (the next read inherits the new generation)."""
    fake = FakeRtlSdr()
    src = _make_source(fake)
    await src.start()

    # Patch read_samples (called inside run_in_executor — a worker thread)
    # to block on a threading Event so we can force a retune mid-read.
    pre_read = threading.Event()
    proceed = threading.Event()
    real_read = fake.read_samples

    def _slow_read(n: int) -> np.ndarray:
        pre_read.set()
        proceed.wait(timeout=2.0)
        return real_read(n)

    fake.read_samples = _slow_read  # type: ignore[assignment]

    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)
    runner = asyncio.create_task(src.run(queue))
    try:
        # Wait (in the asyncio loop) until the worker thread enters _slow_read.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, pre_read.wait)

        # Retune before allowing the in-flight read to complete.
        await src.apply_rf_update(
            RFConfig(
                center_freq_hz=200_000_000, sample_rate_hz=2_400_000, fft_size=1024
            ),
            tuner=None,
        )

        # Restore fast reads, then release the blocked one — its chunk is
        # discarded because of the generation mismatch.
        fake.read_samples = real_read  # type: ignore[assignment]
        proceed.set()
        # A fresh chunk under the new generation should arrive promptly.
        await asyncio.wait_for(queue.get(), timeout=1.0)
    finally:
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, BaseException):
            pass
        await src.stop()
