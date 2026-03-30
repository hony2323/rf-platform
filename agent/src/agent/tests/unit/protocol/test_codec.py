"""Unit tests for JsonBase64Codec.

Contract tests: assert on decoded typed values only.
Never assert JSON string equality or key ordering.
"""

from __future__ import annotations

import base64
import json

import pytest

from agent.domain import (
    AgentMetrics,
    DropCounters,
    FFTSemantics,
    HardwareInfo,
    RFConfig,
    SpectrumFrame,
    WireEncoding,
)
from agent.protocol import (
    ConnectAck,
    Disconnect,
    JsonBase64Codec,
    ServerError,
    StreamConfigAck,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NODE_ID = "node_a1b2c3"
_SESSION_ID = "ses_01HX"
_STREAM_ID = "default"
_TIMESTAMP = "2026-03-26T10:00:00.000Z"
_PROTOCOL_VERSION = "0.3"
_AGENT_VERSION = "0.3.0"

# ---------------------------------------------------------------------------
# Fixtures and factories
# ---------------------------------------------------------------------------


@pytest.fixture
def codec() -> JsonBase64Codec:
    return JsonBase64Codec()


def make_payload_bytes(n: int = 16) -> bytes:
    return bytes(range(n))


def make_agent_metrics() -> AgentMetrics:
    return AgentMetrics(
        cpu_usage_pct=34.0,
        throttled=False,
        tx_bytes_per_sec=820_000,
        queue_depth=3,
        queue_fill_pct=12.0,
        drops=DropCounters(
            local_throttle=0,
            queue_overflow=1,
            server_rejected=2,
        ),
    )


def make_fft_semantics() -> FFTSemantics:
    return FFTSemantics()


# ---------------------------------------------------------------------------
# 1. Roundtrip: outbound messages (encode → json.loads → assert field values)
# ---------------------------------------------------------------------------


def test_encode_connect_roundtrip(codec: JsonBase64Codec) -> None:
    hw = HardwareInfo(vendor="RTL-SDR", model="RTL2832U", serial="00000001")
    raw = codec.encode_connect(
        node_id=_NODE_ID,
        protocol_version=_PROTOCOL_VERSION,
        agent_version=_AGENT_VERSION,
        requested_encoding=WireEncoding.JSON_BASE64,
        hardware=hw,
    )
    msg = json.loads(raw)
    assert msg["msg_type"] == "connect"
    assert msg["protocol_version"] == "0.3"
    assert msg["node_id"] == _NODE_ID
    assert msg["agent_version"] == _AGENT_VERSION
    assert msg["requested_encoding"] == "json_base64"
    assert msg["hardware"]["vendor"] == "RTL-SDR"
    assert msg["hardware"]["model"] == "RTL2832U"
    assert msg["hardware"]["serial"] == "00000001"


def test_encode_connect_roundtrip_without_hardware(codec: JsonBase64Codec) -> None:
    raw = codec.encode_connect(
        node_id=_NODE_ID,
        protocol_version=_PROTOCOL_VERSION,
        agent_version=_AGENT_VERSION,
        requested_encoding=WireEncoding.JSON_BASE64,
        hardware=None,
    )
    msg = json.loads(raw)
    assert msg["msg_type"] == "connect"
    assert msg["node_id"] == _NODE_ID
    assert "hardware" not in msg


def test_encode_stream_config_roundtrip(codec: JsonBase64Codec) -> None:
    rf = RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=1024,
    )
    sem = make_fft_semantics()
    raw = codec.encode_stream_config(
        node_id=_NODE_ID,
        session_id=_SESSION_ID,
        stream_id=_STREAM_ID,
        timestamp_utc=_TIMESTAMP,
        rf_config=rf,
        fft_semantics=sem,
    )
    msg = json.loads(raw)
    assert msg["msg_type"] == "stream_config"
    assert msg["node_id"] == _NODE_ID
    assert msg["session_id"] == _SESSION_ID
    assert msg["stream_id"] == _STREAM_ID
    assert msg["timestamp_utc"] == _TIMESTAMP
    # config_version must NOT be present — server assigns it
    assert "config_version" not in msg
    # rf fields
    assert msg["rf"]["center_freq_hz"] == 433_920_000
    assert msg["rf"]["sample_rate_hz"] == 2_400_000
    assert msg["rf"]["fft_size"] == 1024
    assert msg["rf"]["baseband_start_hz"] == pytest.approx(-1_200_000.0)
    assert msg["rf"]["baseband_end_hz"] == pytest.approx(1_200_000.0)
    assert msg["rf"]["bin_size_hz"] == pytest.approx(2_400_000 / 1024)
    # effective_bin_count == fft_size when bin_count=None
    assert msg["rf"]["bin_count"] == 1024
    assert msg["rf"]["window_fn"] == "hann"
    # fft_semantics
    assert msg["fft_semantics"]["kind"] == "power"
    assert msg["fft_semantics"]["scale"] == "log"
    assert msg["fft_semantics"]["unit"] == "dBFS"
    assert msg["fft_semantics"]["numeric_type"] == "float32"
    assert msg["fft_semantics"]["bin_order"] == "low_to_high"


def test_encode_spectrum_frame_roundtrip_decodes_payload_back_to_original_bytes(
    codec: JsonBase64Codec,
) -> None:
    payload = make_payload_bytes(64)  # 64 bytes = 16 float32 bins
    frame = SpectrumFrame(payload=payload, timestamp_utc=_TIMESTAMP, bin_count=16)
    raw = codec.encode_spectrum_frame(
        node_id=_NODE_ID,
        session_id=_SESSION_ID,
        stream_id=_STREAM_ID,
        config_version=1,
        frame_index=0,
        frame=frame,
    )
    msg = json.loads(raw)
    assert msg["msg_type"] == "spectrum_frame"
    assert msg["node_id"] == _NODE_ID
    assert msg["session_id"] == _SESSION_ID
    assert msg["stream_id"] == _STREAM_ID
    assert msg["config_version"] == 1
    assert msg["frame_index"] == 0
    assert msg["timestamp_utc"] == _TIMESTAMP
    decoded_payload = base64.b64decode(msg["data"]["payload"])
    assert decoded_payload == payload


def test_encode_heartbeat_roundtrip(codec: JsonBase64Codec) -> None:
    raw = codec.encode_heartbeat(
        node_id=_NODE_ID,
        session_id=_SESSION_ID,
        timestamp_utc=_TIMESTAMP,
    )
    msg = json.loads(raw)
    assert msg["msg_type"] == "heartbeat"
    assert msg["node_id"] == _NODE_ID
    assert msg["session_id"] == _SESSION_ID
    assert msg["timestamp_utc"] == _TIMESTAMP
    assert "stream_id" not in msg


def test_encode_agent_status_roundtrip(codec: JsonBase64Codec) -> None:
    metrics = make_agent_metrics()
    raw = codec.encode_agent_status(
        node_id=_NODE_ID,
        session_id=_SESSION_ID,
        timestamp_utc=_TIMESTAMP,
        metrics=metrics,
    )
    msg = json.loads(raw)
    assert msg["msg_type"] == "agent_status"
    assert msg["node_id"] == _NODE_ID
    assert msg["session_id"] == _SESSION_ID
    assert msg["timestamp_utc"] == _TIMESTAMP
    assert msg["cpu_usage_pct"] == pytest.approx(34.0)
    assert msg["throttled"] is False
    assert msg["tx_bytes_per_sec"] == 820_000
    assert msg["queue_depth"] == 3
    assert msg["queue_fill_pct"] == pytest.approx(12.0)
    assert msg["drops"]["local_throttle"] == 0
    assert msg["drops"]["queue_overflow"] == 1
    assert msg["drops"]["server_rejected"] == 2


# ---------------------------------------------------------------------------
# 2. Roundtrip: inbound messages (build wire dict → decode → assert typed fields)
# ---------------------------------------------------------------------------


def test_decode_connect_ack_roundtrip(codec: JsonBase64Codec) -> None:
    wire = {
        "msg_type": "connect_ack",
        "session_id": _SESSION_ID,
        "status": "ok",
        "wire_encoding": "json_base64",
    }
    result = codec.decode(json.dumps(wire))
    assert isinstance(result, ConnectAck)
    assert result.session_id == _SESSION_ID
    assert result.status == "ok"
    assert result.wire_encoding == WireEncoding.JSON_BASE64


def test_decode_stream_config_ack_roundtrip(codec: JsonBase64Codec) -> None:
    wire = {
        "msg_type": "stream_config_ack",
        "session_id": _SESSION_ID,
        "stream_id": _STREAM_ID,
        "config_version": 1,
        "status": "ok",
    }
    result = codec.decode(json.dumps(wire))
    assert isinstance(result, StreamConfigAck)
    assert result.session_id == _SESSION_ID
    assert result.stream_id == _STREAM_ID
    assert result.config_version == 1
    assert result.status == "ok"


def test_decode_disconnect_roundtrip(codec: JsonBase64Codec) -> None:
    wire = {
        "msg_type": "disconnect",
        "session_id": _SESSION_ID,
        "reason": "auth_expired",
    }
    result = codec.decode(json.dumps(wire))
    assert isinstance(result, Disconnect)
    assert result.session_id == _SESSION_ID
    assert result.reason == "auth_expired"


def test_decode_error_roundtrip_with_optional_fields_present(
    codec: JsonBase64Codec,
) -> None:
    wire = {
        "msg_type": "error",
        "session_id": _SESSION_ID,
        "stream_id": _STREAM_ID,
        "config_version": 2,
        "frame_index": 1024,
        "code": "INVALID_FRAME",
        "message": "payload length does not match bin_count",
        "fatal": False,
    }
    result = codec.decode(json.dumps(wire))
    assert isinstance(result, ServerError)
    assert result.session_id == _SESSION_ID
    assert result.stream_id == _STREAM_ID
    assert result.config_version == 2
    assert result.frame_index == 1024
    assert result.code == "INVALID_FRAME"
    assert result.message == "payload length does not match bin_count"
    assert result.fatal is False


def test_decode_error_roundtrip_with_optional_fields_omitted(
    codec: JsonBase64Codec,
) -> None:
    wire = {
        "msg_type": "error",
        "session_id": _SESSION_ID,
        "code": "PROTOCOL_MISMATCH",
        "message": "unsupported protocol version",
        "fatal": True,
    }
    result = codec.decode(json.dumps(wire))
    assert isinstance(result, ServerError)
    assert result.session_id == _SESSION_ID
    assert result.code == "PROTOCOL_MISMATCH"
    assert result.fatal is True
    assert result.stream_id is None
    assert result.config_version is None
    assert result.frame_index is None


# ---------------------------------------------------------------------------
# 3. Validation / failure tests
# ---------------------------------------------------------------------------


def test_decode_rejects_unknown_msg_type(codec: JsonBase64Codec) -> None:
    wire = json.dumps({"msg_type": "bogus", "node_id": _NODE_ID})
    with pytest.raises(ValueError):
        codec.decode(wire)


def test_decode_rejects_invalid_json(codec: JsonBase64Codec) -> None:
    with pytest.raises(ValueError):
        codec.decode("not valid json {")


def test_decode_rejects_message_missing_required_fields(codec: JsonBase64Codec) -> None:
    # connect_ack missing session_id and wire_encoding
    wire = json.dumps({"msg_type": "connect_ack", "status": "ok"})
    with pytest.raises(ValueError):
        codec.decode(wire)


def test_decode_rejects_invalid_base64_payload_in_spectrum_frame(
    codec: JsonBase64Codec,
) -> None:
    # spectrum_frame is an outbound-only message type — decode must reject it
    wire = json.dumps(
        {"msg_type": "spectrum_frame", "data": {"payload": "!!!invalid_base64!!!"}}
    )
    with pytest.raises(ValueError):
        codec.decode(wire)


def test_decode_rejects_spectrum_frame_missing_data_payload(
    codec: JsonBase64Codec,
) -> None:
    wire = json.dumps({"msg_type": "spectrum_frame"})
    with pytest.raises(ValueError):
        codec.decode(wire)


def test_decode_rejects_error_with_non_boolean_fatal(codec: JsonBase64Codec) -> None:
    # fatal=1 is an int, not bool — must be rejected
    wire = json.dumps(
        {
            "msg_type": "error",
            "session_id": _SESSION_ID,
            "code": "INVALID_FRAME",
            "message": "test",
            "fatal": 1,
        }
    )
    with pytest.raises(ValueError):
        codec.decode(wire)


# ---------------------------------------------------------------------------
# 4. Payload tests
# ---------------------------------------------------------------------------


def test_json_base64_payload_length_matches_bin_count_times_four_after_decode(
    codec: JsonBase64Codec,
) -> None:
    bin_count = 16
    payload = make_payload_bytes(bin_count * 4)
    frame = SpectrumFrame(
        payload=payload, timestamp_utc=_TIMESTAMP, bin_count=bin_count
    )
    raw = codec.encode_spectrum_frame(
        node_id=_NODE_ID,
        session_id=_SESSION_ID,
        stream_id=_STREAM_ID,
        config_version=1,
        frame_index=0,
        frame=frame,
    )
    msg = json.loads(raw)
    decoded = base64.b64decode(msg["data"]["payload"])
    assert len(decoded) == bin_count * 4
