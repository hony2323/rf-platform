"""Unit tests for AgentConfig and its sub-config dataclasses."""

from __future__ import annotations

import dataclasses

import pytest

from agent.config import (
    AgentConfig,
    AgentIdentity,
    QueueConfig,
    ReconnectConfig,
    ServerConfig,
    TelemetryConfig,
)
from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    WireEncoding,
    WindowFunction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity() -> AgentIdentity:
    return AgentIdentity(node_id="test-node-001")


def _make_server() -> ServerConfig:
    return ServerConfig(url="wss://example.com/ws", token="test-token")


def _make_rf() -> RFConfig:
    return RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
    )


def _make_iq() -> IQDescriptor:
    return IQDescriptor(
        sample_format=SampleFormat.FLOAT32,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=2_400_000,
        center_freq_hz=433_920_000,
    )


def _make_agent_config(**kwargs: object) -> AgentConfig:
    base = {
        "identity": _make_identity(),
        "server": _make_server(),
        "rf": _make_rf(),
        "iq": _make_iq(),
    }
    base.update(kwargs)
    return AgentConfig(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------


def test_agent_identity_stores_node_id() -> None:
    identity = AgentIdentity(node_id="edge-001")
    assert identity.node_id == "edge-001"


def test_agent_identity_default_version() -> None:
    identity = AgentIdentity(node_id="edge-001")
    assert identity.agent_version == "0.3.0"


def test_agent_identity_is_frozen() -> None:
    identity = AgentIdentity(node_id="edge-001")
    with pytest.raises(dataclasses.FrozenInstanceError):
        identity.node_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ServerConfig
# ---------------------------------------------------------------------------


def test_server_config_stores_url_and_token() -> None:
    cfg = ServerConfig(url="wss://host/ws", token="bearer-abc")
    assert cfg.url == "wss://host/ws"
    assert cfg.token == "bearer-abc"


def test_server_config_is_frozen() -> None:
    cfg = ServerConfig(url="wss://host/ws", token="t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.url = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# QueueConfig
# ---------------------------------------------------------------------------


def test_queue_config_defaults() -> None:
    q = QueueConfig()
    assert q.iq_queue_size == 4
    assert q.frame_queue_size == 8


def test_queue_config_accepts_custom_sizes() -> None:
    q = QueueConfig(iq_queue_size=2, frame_queue_size=16)
    assert q.iq_queue_size == 2
    assert q.frame_queue_size == 16


# ---------------------------------------------------------------------------
# TelemetryConfig
# ---------------------------------------------------------------------------


def test_telemetry_config_defaults() -> None:
    t = TelemetryConfig()
    assert t.heartbeat_interval_s == pytest.approx(5.0)
    assert t.status_interval_s == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# ReconnectConfig
# ---------------------------------------------------------------------------


def test_reconnect_config_defaults() -> None:
    r = ReconnectConfig()
    assert r.initial_delay_s == pytest.approx(1.0)
    assert r.max_delay_s == pytest.approx(30.0)
    assert r.backoff_factor == pytest.approx(2.0)
    assert r.jitter is True


# ---------------------------------------------------------------------------
# RFConfig computed properties
# ---------------------------------------------------------------------------


def test_rf_config_bin_size_hz() -> None:
    cfg = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
    )
    assert cfg.bin_size_hz == pytest.approx(2_400_000 / 1024)


def test_rf_config_baseband_edges_are_symmetric() -> None:
    cfg = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
    )
    assert cfg.baseband_start_hz == pytest.approx(-1_200_000.0)
    assert cfg.baseband_end_hz == pytest.approx(1_200_000.0)


def test_rf_config_baseband_span_equals_sample_rate() -> None:
    cfg = RFConfig(
        center_freq_hz=100_000_000,
        sample_rate_hz=30_720_000,
        fft_size=2048,
    )
    span = cfg.baseband_end_hz - cfg.baseband_start_hz
    assert span == pytest.approx(30_720_000.0)


def test_rf_config_default_window_is_hann() -> None:
    cfg = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
    )
    assert cfg.window_fn == WindowFunction.HANN


def test_rf_config_is_frozen() -> None:
    cfg = _make_rf()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.fft_size = 512  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AgentConfig assembly
# ---------------------------------------------------------------------------


def test_agent_config_default_wire_encoding() -> None:
    cfg = _make_agent_config()
    assert cfg.wire_encoding == WireEncoding.JSON_BASE64


def test_agent_config_uses_default_sub_configs() -> None:
    cfg = _make_agent_config()
    assert isinstance(cfg.queues, QueueConfig)
    assert isinstance(cfg.telemetry, TelemetryConfig)
    assert isinstance(cfg.reconnect, ReconnectConfig)


def test_agent_config_default_sub_configs_are_independent_instances() -> None:
    cfg_a = _make_agent_config()
    cfg_b = _make_agent_config()
    # field(default_factory=...) must produce separate instances
    assert cfg_a.queues is not cfg_b.queues
    assert cfg_a.telemetry is not cfg_b.telemetry
    assert cfg_a.reconnect is not cfg_b.reconnect


def test_agent_config_accepts_custom_sub_configs() -> None:
    q = QueueConfig(iq_queue_size=1, frame_queue_size=2)
    cfg = _make_agent_config(queues=q)
    assert cfg.queues.iq_queue_size == 1
    assert cfg.queues.frame_queue_size == 2


def test_agent_config_is_frozen() -> None:
    cfg = _make_agent_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.wire_encoding = WireEncoding.JSON_BASE64  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RFConfig.bin_count / effective_bin_count  (issue #5)
# ---------------------------------------------------------------------------


def test_rf_config_default_bin_count_is_none() -> None:
    cfg = _make_rf()
    assert cfg.bin_count is None


def test_rf_config_effective_bin_count_defaults_to_fft_size() -> None:
    cfg = _make_rf()  # bin_count=None
    assert cfg.effective_bin_count == cfg.fft_size


def test_rf_config_effective_bin_count_uses_explicit_value() -> None:
    cfg = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
        bin_count=512,
    )
    assert cfg.effective_bin_count == 512


def test_rf_config_effective_bin_count_does_not_equal_fft_size_when_set() -> None:
    cfg = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
        bin_count=512,
    )
    assert cfg.effective_bin_count != cfg.fft_size


# ---------------------------------------------------------------------------
# AgentConfig.stream_id  (issue #6)
# ---------------------------------------------------------------------------


def test_agent_config_default_stream_id() -> None:
    cfg = _make_agent_config()
    assert cfg.stream_id == "default"


def test_agent_config_accepts_custom_stream_id() -> None:
    cfg = _make_agent_config(stream_id="antenna-2")
    assert cfg.stream_id == "antenna-2"
