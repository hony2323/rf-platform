"""Shared pytest fixtures for all agent tests."""

from __future__ import annotations

from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sigmf_lte_meta_path() -> Path:
    """Path to the trimmed LTE uplink SigMF fixture (ci16_le, 847 MHz, 30.72 Msps).

    This is a reduced-size recording suitable for CI. Full-length recordings
    belong in recordings/ at the repo root (gitignored).
    """
    return (
        _FIXTURES_DIR
        / "LTE_uplink_847MHz_2022-01-30_30720ksps_fix"
        / "LTE_uplink_847MHz_2022-01-30_30720ksps.sigmf-meta"
    )
