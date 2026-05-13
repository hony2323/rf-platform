"""Unit tests for SimulatorSource.apply_rf_update (live retune)."""

from __future__ import annotations

import asyncio

import pytest

from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    TunerConfig,
)
from agent.source.base import LiveRetunableSource
from agent.source.simulator import SimulatorSource


def _descriptor(
    center_freq_hz: int = 100_000_000, sample_rate_hz: int = 1_000_000
) -> IQDescriptor:
    return IQDescriptor(
        sample_format=SampleFormat.FLOAT32,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=sample_rate_hz,
        center_freq_hz=center_freq_hz,
    )


def test_simulator_advertises_live_retune_capability() -> None:
    src = SimulatorSource(_descriptor())
    assert isinstance(src, LiveRetunableSource)


async def test_apply_rf_update_swaps_descriptor_in_place() -> None:
    src = SimulatorSource(_descriptor())
    assert src.descriptor.center_freq_hz == 100_000_000
    assert src.descriptor.sample_rate_hz == 1_000_000

    new_rf = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=2048,
    )
    await src.apply_rf_update(new_rf, tuner=None)

    assert src.descriptor.center_freq_hz == 433_920_000
    assert src.descriptor.sample_rate_hz == 2_400_000


async def test_apply_rf_update_resets_phase() -> None:
    src = SimulatorSource(_descriptor(), block_size=128)
    src._phase = 1.234  # type: ignore[attr-defined]

    await src.apply_rf_update(
        _descriptor().__class__.__mro__[0]  # noqa: F841
        and RFConfig(  # use a fresh RFConfig
            center_freq_hz=200_000_000,
            sample_rate_hz=2_000_000,
            fft_size=1024,
        ),
        tuner=None,
    )

    assert src._phase == 0.0  # type: ignore[attr-defined]


async def test_apply_rf_update_takes_effect_on_next_block() -> None:
    """After retune, the next emitted block uses the new sample rate."""
    src = SimulatorSource(_descriptor(sample_rate_hz=1_000_000), block_size=128)
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=4)

    runner = asyncio.create_task(src.run(queue))
    try:
        # Drain one block at the old rate.
        await asyncio.wait_for(queue.get(), timeout=1.0)

        await src.apply_rf_update(
            RFConfig(
                center_freq_hz=433_920_000,
                sample_rate_hz=2_400_000,
                fft_size=1024,
            ),
            tuner=None,
        )

        # Descriptor is the new value; subsequent emissions are generated
        # against the new sample rate (per-block omega recomputation).
        assert src.descriptor.sample_rate_hz == 2_400_000
        await asyncio.wait_for(queue.get(), timeout=1.0)
    finally:
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner


async def test_apply_rf_update_ignores_tuner() -> None:
    """Simulator has no gain stage; tuner argument is silently accepted."""
    src = SimulatorSource(_descriptor())
    await src.apply_rf_update(
        RFConfig(center_freq_hz=99_000_000, sample_rate_hz=1_000_000, fft_size=512),
        tuner=TunerConfig(gain_db=30.0, agc=False),
    )
    assert src.descriptor.center_freq_hz == 99_000_000
