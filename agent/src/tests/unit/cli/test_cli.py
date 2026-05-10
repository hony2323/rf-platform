"""Unit tests for agent CLI layer."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from agent.cli import _resolve_connect
from agent.domain import Endianness, IQDescriptor, Layout, SampleFormat


def _minimal_args(**kwargs: object) -> argparse.Namespace:
    defaults = {
        "server": None,
        "token": None,
        "node_id": None,
        "file": None,
        "fps": None,
        "rate_limit_msps": None,
        "fft_size": None,
        "sample_rate": None,
        "freq": None,
        "source": None,
        "rtlsdr_device_index": None,
        "rtlsdr_gain": None,
        "rtlsdr_chunk_samples": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestCliPrecedence:
    def test_cli_over_config_over_env_for_server_and_token(self) -> None:
        """CLI flags beat config file; config file beats env vars."""
        file_cfg = {
            "server": {"url": "ws://from-config:8000/ws/agent", "token": "cfg-token"},
            "identity": {"node_id": "test-node"},
        }
        env = {
            "RF_AGENT_SERVER": "ws://from-env:8000/ws/agent",
            "RF_AGENT_TOKEN": "env-token",
        }

        # Config beats env (no CLI flags set)
        args_no_cli = _minimal_args()
        with patch.dict("os.environ", env, clear=False):
            cfg, _, _ = _resolve_connect(args_no_cli, file_cfg)
        assert cfg.server.url == "ws://from-config:8000/ws/agent"
        assert cfg.server.token == "cfg-token"

        # CLI beats config
        args_with_cli = _minimal_args(
            server="ws://from-cli:8000/ws/agent", token="cli-token"
        )
        with patch.dict("os.environ", env, clear=False):
            cfg2, _, _ = _resolve_connect(args_with_cli, file_cfg)
        assert cfg2.server.url == "ws://from-cli:8000/ws/agent"
        assert cfg2.server.token == "cli-token"


class TestSimulatorSourceValidation:
    def test_rejects_non_float32_little_interleaved_descriptor(self) -> None:
        from agent.source.simulator import SimulatorSource

        bad_descriptor = IQDescriptor(
            sample_format=SampleFormat.INT16,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=240_000,
            center_freq_hz=433_920_000,
        )
        with pytest.raises(ValueError, match="FLOAT32"):
            SimulatorSource(descriptor=bad_descriptor)

    def test_accepts_float32_little_interleaved(self) -> None:
        from agent.source.simulator import SimulatorSource

        good_descriptor = IQDescriptor(
            sample_format=SampleFormat.FLOAT32,
            endianness=Endianness.LITTLE,
            layout=Layout.INTERLEAVED,
            sample_rate_hz=240_000,
            center_freq_hz=433_920_000,
        )
        src = SimulatorSource(descriptor=good_descriptor)
        assert src.descriptor is good_descriptor


class TestCliUsesConfigLoader:
    def test_rejects_invalid_cross_checked_config(self) -> None:
        """load_config_dict cross-check: iq.sample_rate_hz must equal rf.sample_rate_hz.

        The CLI builds the raw dict so both fields come from the same resolved
        sample_rate value — they always match. This test verifies that if an
        injected file_cfg overrides iq but not rf (which can't happen through
        normal config, but covers the validation path), SystemExit is raised.
        We force the mismatch by monkey-patching load_config_dict.
        """
        from unittest.mock import patch

        from agent.config import ConfigValidationError

        file_cfg: dict = {
            "server": {"url": "ws://localhost:8000/ws/agent", "token": "tok"},
            "identity": {"node_id": "node1"},
        }
        args = _minimal_args()

        with patch(
            "agent.cli.load_config_dict",
            side_effect=ConfigValidationError(
                "iq.sample_rate_hz: must equal rf.sample_rate_hz"
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _resolve_connect(args, file_cfg)
        assert "Configuration error" in str(exc_info.value)
