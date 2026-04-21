"""Tests for the config loading boundary (load_config_dict).

These are config-boundary tests only.  They verify that raw external input
is correctly validated, coerced, and rejected with clear error messages.
They do NOT test parser / session / FFT behaviour.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.config import ConfigValidationError, load_config_dict
from agent.config import (
    AgentConfig,
    BandwidthConfig,
    QueueConfig,
    ReconnectConfig,
    TelemetryConfig,
)
from agent.domain import (
    Endianness,
    Layout,
    RFConfig,
    SampleFormat,
    WireEncoding,
    WindowFunction,
)


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


def _valid_raw() -> dict[str, Any]:
    """Return a minimal valid raw config dict."""
    return {
        "server": {"url": "wss://example.com/ws", "token": "secret"},
        "identity": {"node_id": "node-001"},
        "iq": {
            "sample_format": "float32",
            "endianness": "little",
            "layout": "interleaved",
            "sample_rate_hz": 2_400_000,
            "center_freq_hz": 433_920_000,
        },
        "rf": {
            "center_freq_hz": 433_920_000,
            "sample_rate_hz": 2_400_000,
            "fft_size": 1024,
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_config_builds_typed_agent_config_from_valid_raw_dict() -> None:
    cfg = load_config_dict(_valid_raw())

    assert isinstance(cfg, AgentConfig)
    assert cfg.server.url == "wss://example.com/ws"
    assert cfg.server.token == "secret"
    assert cfg.identity.node_id == "node-001"
    assert cfg.iq.sample_format == SampleFormat.FLOAT32
    assert cfg.iq.endianness == Endianness.LITTLE
    assert cfg.iq.layout == Layout.INTERLEAVED
    assert cfg.iq.sample_rate_hz == 2_400_000
    assert cfg.iq.center_freq_hz == 433_920_000
    assert isinstance(cfg.rf, RFConfig)
    assert cfg.rf.fft_size == 1024


def test_load_config_applies_defaults_for_optional_fields() -> None:
    cfg = load_config_dict(_valid_raw())

    assert cfg.identity.agent_version == "0.3.0"
    assert cfg.stream_id == "default"
    assert cfg.wire_encoding == WireEncoding.JSON_BASE64
    assert cfg.iq.dc_offset_remove is True
    assert cfg.iq.normalize is True
    assert cfg.rf.window_fn == WindowFunction.HANN
    assert cfg.rf.bin_count is None
    assert isinstance(cfg.queues, QueueConfig)
    assert isinstance(cfg.telemetry, TelemetryConfig)
    assert isinstance(cfg.reconnect, ReconnectConfig)


# ---------------------------------------------------------------------------
# Enum boundary
# ---------------------------------------------------------------------------


def test_config_rejects_unsupported_sample_format_string() -> None:
    raw = _valid_raw()
    raw["iq"]["sample_format"] = "int8"
    with pytest.raises(ConfigValidationError, match="iq.sample_format"):
        load_config_dict(raw)


def test_config_rejects_unsupported_endianness_string() -> None:
    raw = _valid_raw()
    raw["iq"]["endianness"] = "middle"
    with pytest.raises(ConfigValidationError, match="iq.endianness"):
        load_config_dict(raw)


def test_config_rejects_unsupported_layout_string() -> None:
    raw = _valid_raw()
    raw["iq"]["layout"] = "planar"
    with pytest.raises(ConfigValidationError, match="iq.layout"):
        load_config_dict(raw)


def test_config_rejects_unsupported_window_function_string() -> None:
    raw = _valid_raw()
    raw["rf"]["window_fn"] = "blackman"
    with pytest.raises(ConfigValidationError, match="rf.window_fn"):
        load_config_dict(raw)


def test_config_rejects_unsupported_wire_encoding_string() -> None:
    raw = _valid_raw()
    raw["wire_encoding"] = "msgpack"
    with pytest.raises(ConfigValidationError, match="wire_encoding"):
        load_config_dict(raw)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_config_rejects_missing_server_url() -> None:
    raw = _valid_raw()
    del raw["server"]["url"]
    with pytest.raises(ConfigValidationError, match="server.url"):
        load_config_dict(raw)


def test_config_rejects_missing_server_token() -> None:
    raw = _valid_raw()
    del raw["server"]["token"]
    with pytest.raises(ConfigValidationError, match="server.token"):
        load_config_dict(raw)


def test_config_rejects_missing_node_id() -> None:
    raw = _valid_raw()
    del raw["identity"]["node_id"]
    with pytest.raises(ConfigValidationError, match="identity.node_id"):
        load_config_dict(raw)


def test_config_rejects_missing_iq_sample_rate_hz() -> None:
    raw = _valid_raw()
    del raw["iq"]["sample_rate_hz"]
    with pytest.raises(ConfigValidationError, match="iq.sample_rate_hz"):
        load_config_dict(raw)


def test_config_rejects_missing_iq_center_freq_hz() -> None:
    raw = _valid_raw()
    del raw["iq"]["center_freq_hz"]
    with pytest.raises(ConfigValidationError, match="iq.center_freq_hz"):
        load_config_dict(raw)


def test_config_rejects_missing_rf_sample_rate_hz() -> None:
    raw = _valid_raw()
    del raw["rf"]["sample_rate_hz"]
    with pytest.raises(ConfigValidationError, match="rf.sample_rate_hz"):
        load_config_dict(raw)


def test_config_rejects_missing_rf_center_freq_hz() -> None:
    raw = _valid_raw()
    del raw["rf"]["center_freq_hz"]
    with pytest.raises(ConfigValidationError, match="rf.center_freq_hz"):
        load_config_dict(raw)


def test_config_rejects_missing_rf_fft_size() -> None:
    raw = _valid_raw()
    del raw["rf"]["fft_size"]
    with pytest.raises(ConfigValidationError, match="rf.fft_size"):
        load_config_dict(raw)


# ---------------------------------------------------------------------------
# Type / value validation
# ---------------------------------------------------------------------------


def test_config_rejects_non_string_server_url() -> None:
    raw = _valid_raw()
    raw["server"]["url"] = 12345
    with pytest.raises(ConfigValidationError, match="server.url"):
        load_config_dict(raw)


def test_config_rejects_empty_server_url() -> None:
    raw = _valid_raw()
    raw["server"]["url"] = ""
    with pytest.raises(ConfigValidationError, match="server.url"):
        load_config_dict(raw)


def test_config_rejects_url_without_ws_scheme() -> None:
    raw = _valid_raw()
    raw["server"]["url"] = "https://example.com/ws"
    with pytest.raises(ConfigValidationError, match="server.url"):
        load_config_dict(raw)


def test_config_rejects_empty_token() -> None:
    raw = _valid_raw()
    raw["server"]["token"] = ""
    with pytest.raises(ConfigValidationError, match="server.token"):
        load_config_dict(raw)


def test_config_rejects_empty_stream_id() -> None:
    raw = _valid_raw()
    raw["stream_id"] = ""
    with pytest.raises(ConfigValidationError, match="stream_id"):
        load_config_dict(raw)


def test_config_rejects_non_positive_iq_sample_rate_hz() -> None:
    raw = _valid_raw()
    raw["iq"]["sample_rate_hz"] = 0
    with pytest.raises(ConfigValidationError, match="iq.sample_rate_hz"):
        load_config_dict(raw)


def test_config_rejects_non_positive_rf_sample_rate_hz() -> None:
    raw = _valid_raw()
    raw["rf"]["sample_rate_hz"] = -1
    with pytest.raises(ConfigValidationError, match="rf.sample_rate_hz"):
        load_config_dict(raw)


def test_config_rejects_non_positive_center_freq_hz() -> None:
    raw = _valid_raw()
    raw["iq"]["center_freq_hz"] = 0
    raw["rf"]["center_freq_hz"] = 0
    with pytest.raises(ConfigValidationError, match="center_freq_hz"):
        load_config_dict(raw)


def test_config_rejects_non_positive_fft_size() -> None:
    raw = _valid_raw()
    raw["rf"]["fft_size"] = 0
    with pytest.raises(ConfigValidationError, match="rf.fft_size"):
        load_config_dict(raw)


def test_config_rejects_non_positive_bin_count() -> None:
    raw = _valid_raw()
    raw["rf"]["bin_count"] = 0
    with pytest.raises(ConfigValidationError, match="rf.bin_count"):
        load_config_dict(raw)


def test_config_rejects_bin_count_greater_than_fft_size() -> None:
    raw = _valid_raw()
    raw["rf"]["bin_count"] = 2048  # fft_size is 1024
    with pytest.raises(ConfigValidationError, match="bin_count"):
        load_config_dict(raw)


def test_config_rejects_non_positive_queue_sizes() -> None:
    raw = _valid_raw()
    raw["queues"] = {"iq_queue_size": 0, "frame_queue_size": 8}
    with pytest.raises(ConfigValidationError, match="queues.iq_queue_size"):
        load_config_dict(raw)


def test_config_rejects_non_positive_telemetry_intervals() -> None:
    raw = _valid_raw()
    raw["telemetry"] = {"heartbeat_interval_s": 0, "status_interval_s": 10.0}
    with pytest.raises(ConfigValidationError, match="telemetry.heartbeat_interval_s"):
        load_config_dict(raw)


def test_config_rejects_invalid_reconnect_range() -> None:
    raw = _valid_raw()
    raw["reconnect"] = {
        "initial_delay_s": 30.0,
        "max_delay_s": 1.0,  # less than initial
        "backoff_factor": 2.0,
    }
    with pytest.raises(ConfigValidationError, match="max_delay_s"):
        load_config_dict(raw)


def test_config_rejects_backoff_factor_less_than_one() -> None:
    raw = _valid_raw()
    raw["reconnect"] = {
        "initial_delay_s": 1.0,
        "max_delay_s": 30.0,
        "backoff_factor": 0.5,
    }
    with pytest.raises(ConfigValidationError, match="backoff_factor"):
        load_config_dict(raw)


# ---------------------------------------------------------------------------
# Cross-field consistency
# ---------------------------------------------------------------------------


def test_config_rejects_iq_and_rf_sample_rate_mismatch() -> None:
    raw = _valid_raw()
    raw["iq"]["sample_rate_hz"] = 1_000_000
    # rf.sample_rate_hz stays at 2_400_000
    with pytest.raises(ConfigValidationError, match="sample_rate_hz"):
        load_config_dict(raw)


def test_config_rejects_iq_and_rf_center_freq_mismatch() -> None:
    raw = _valid_raw()
    raw["iq"]["center_freq_hz"] = 100_000_000
    # rf.center_freq_hz stays at 433_920_000
    with pytest.raises(ConfigValidationError, match="center_freq_hz"):
        load_config_dict(raw)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_config_defaults_stream_id_to_default() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.stream_id == "default"


def test_config_defaults_wire_encoding_to_json_base64() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.wire_encoding == WireEncoding.JSON_BASE64


def test_config_defaults_iq_dc_offset_remove_to_true() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.iq.dc_offset_remove is True


def test_config_defaults_iq_normalize_to_true() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.iq.normalize is True


def test_config_defaults_queue_config() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.queues.iq_queue_size == 4
    assert cfg.queues.frame_queue_size == 8


def test_config_defaults_telemetry_config() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.telemetry.heartbeat_interval_s == pytest.approx(5.0)
    assert cfg.telemetry.status_interval_s == pytest.approx(10.0)


def test_config_defaults_reconnect_config() -> None:
    cfg = load_config_dict(_valid_raw())
    assert cfg.reconnect.initial_delay_s == pytest.approx(1.0)
    assert cfg.reconnect.max_delay_s == pytest.approx(30.0)
    assert cfg.reconnect.backoff_factor == pytest.approx(2.0)
    assert cfg.reconnect.jitter is True


# ---------------------------------------------------------------------------
# Bool validation
# ---------------------------------------------------------------------------


def test_config_rejects_non_bool_iq_dc_offset_remove() -> None:
    for bad in ("yes", 1, 0, []):
        raw = _valid_raw()
        raw["iq"]["dc_offset_remove"] = bad
        with pytest.raises(ConfigValidationError, match="iq.dc_offset_remove"):
            load_config_dict(raw)


def test_config_rejects_non_bool_iq_normalize() -> None:
    for bad in ("true", 1, 0, None):
        raw = _valid_raw()
        raw["iq"]["normalize"] = bad
        with pytest.raises(ConfigValidationError, match="iq.normalize"):
            load_config_dict(raw)


def test_config_rejects_non_bool_reconnect_jitter() -> None:
    for bad in ("yes", 1, 0):
        raw = _valid_raw()
        raw["reconnect"] = {
            "initial_delay_s": 1.0,
            "max_delay_s": 30.0,
            "backoff_factor": 2.0,
            "jitter": bad,
        }
        with pytest.raises(ConfigValidationError, match="reconnect.jitter"):
            load_config_dict(raw)


# ---------------------------------------------------------------------------
# Agent version validation
# ---------------------------------------------------------------------------


def test_config_rejects_non_string_agent_version() -> None:
    raw = _valid_raw()
    raw["identity"]["agent_version"] = 330
    with pytest.raises(ConfigValidationError, match="identity.agent_version"):
        load_config_dict(raw)


def test_config_rejects_empty_agent_version() -> None:
    raw = _valid_raw()
    raw["identity"]["agent_version"] = ""
    with pytest.raises(ConfigValidationError, match="identity.agent_version"):
        load_config_dict(raw)


# ---------------------------------------------------------------------------
# Optional section strictness (non-None non-mapping values must be rejected)
# ---------------------------------------------------------------------------


def test_config_rejects_non_mapping_queues_section() -> None:
    for bad in ("", 0, False, "invalid"):
        raw = _valid_raw()
        raw["queues"] = bad
        with pytest.raises(ConfigValidationError, match="queues"):
            load_config_dict(raw)


def test_config_rejects_non_mapping_telemetry_section() -> None:
    for bad in ("", 0, False, "invalid"):
        raw = _valid_raw()
        raw["telemetry"] = bad
        with pytest.raises(ConfigValidationError, match="telemetry"):
            load_config_dict(raw)


def test_config_rejects_non_mapping_reconnect_section() -> None:
    for bad in ("", 0, False, "invalid"):
        raw = _valid_raw()
        raw["reconnect"] = bad
        with pytest.raises(ConfigValidationError, match="reconnect"):
            load_config_dict(raw)


# ---------------------------------------------------------------------------
# Required section type validation
# ---------------------------------------------------------------------------


def test_config_rejects_non_mapping_server_section() -> None:
    raw = _valid_raw()
    raw["server"] = "wss://example.com"
    with pytest.raises(ConfigValidationError, match="server"):
        load_config_dict(raw)


def test_config_rejects_non_mapping_identity_section() -> None:
    raw = _valid_raw()
    raw["identity"] = "node-001"
    with pytest.raises(ConfigValidationError, match="identity"):
        load_config_dict(raw)


def test_config_rejects_non_mapping_iq_section() -> None:
    raw = _valid_raw()
    raw["iq"] = "float32"
    with pytest.raises(ConfigValidationError, match="iq"):
        load_config_dict(raw)


def test_config_rejects_non_mapping_rf_section() -> None:
    raw = _valid_raw()
    raw["rf"] = 1024
    with pytest.raises(ConfigValidationError, match="rf"):
        load_config_dict(raw)


# ---------------------------------------------------------------------------
# Bandwidth config
# ---------------------------------------------------------------------------


def test_config_defaults_bandwidth_to_unlimited() -> None:
    cfg = load_config_dict(_valid_raw())
    assert isinstance(cfg.bandwidth, BandwidthConfig)
    assert cfg.bandwidth.max_bytes_per_sec is None
    assert cfg.bandwidth.strategy == "decimate"


def test_config_loads_bandwidth_with_max_bytes_per_sec() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = {"max_bytes_per_sec": 50_000, "strategy": "drop"}
    cfg = load_config_dict(raw)
    assert cfg.bandwidth.max_bytes_per_sec == 50_000
    assert cfg.bandwidth.strategy == "drop"


def test_config_loads_bandwidth_decimate_strategy() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = {"max_bytes_per_sec": 100_000, "strategy": "decimate"}
    cfg = load_config_dict(raw)
    assert cfg.bandwidth.strategy == "decimate"


def test_config_bandwidth_defaults_strategy_to_decimate_when_absent() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = {"max_bytes_per_sec": 10_000}
    cfg = load_config_dict(raw)
    assert cfg.bandwidth.strategy == "decimate"


def test_config_bandwidth_absent_section_returns_defaults() -> None:
    raw = _valid_raw()
    # Explicitly no bandwidth key
    raw.pop("bandwidth", None)
    cfg = load_config_dict(raw)
    assert cfg.bandwidth.max_bytes_per_sec is None


def test_config_rejects_non_mapping_bandwidth_section() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = "fast"
    with pytest.raises(ConfigValidationError, match="bandwidth"):
        load_config_dict(raw)


def test_config_rejects_non_positive_bandwidth_max_bytes_per_sec() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = {"max_bytes_per_sec": 0}
    with pytest.raises(ConfigValidationError, match="bandwidth.max_bytes_per_sec"):
        load_config_dict(raw)


def test_config_rejects_non_integer_bandwidth_max_bytes_per_sec() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = {"max_bytes_per_sec": 1000.5}
    with pytest.raises(ConfigValidationError, match="bandwidth.max_bytes_per_sec"):
        load_config_dict(raw)


def test_config_rejects_unknown_bandwidth_strategy() -> None:
    raw = _valid_raw()
    raw["bandwidth"] = {"max_bytes_per_sec": 1000, "strategy": "throttle"}
    with pytest.raises(ConfigValidationError, match="bandwidth.strategy"):
        load_config_dict(raw)
