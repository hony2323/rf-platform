"""Verify file-based sources do NOT implement apply_rf_update."""

from __future__ import annotations

from agent.source.base import LiveRetunableSource


def test_wav_source_does_not_implement_live_retune() -> None:
    from agent.source.wav import WavSource

    assert not issubclass(WavSource, LiveRetunableSource)
    assert not hasattr(WavSource, "apply_rf_update")


def test_sigmf_source_does_not_implement_live_retune() -> None:
    from agent.source.sigmf import SigMFSource

    assert not issubclass(SigMFSource, LiveRetunableSource)
    assert not hasattr(SigMFSource, "apply_rf_update")
