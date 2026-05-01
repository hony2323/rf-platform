"""Unit tests for server.protocol.codec.

Contract tests: assert on decoded values only, never on raw byte equality
or JSON key ordering.
"""

from __future__ import annotations

import json
import struct

from server.protocol.codec import (
    ProtocolError,
    SpectrumFrameMsg,
    decode_spectrum_frame_binary,
    encode_viewer_spectrum_frame_binary,
)


_AGENT_ID = "agent_abc"
_SESSION_ID = "ses_01HX"
_STREAM_ID = "default"
_TIMESTAMP = "2026-01-01T00:00:01.000Z"
_BIN_COUNT = 16
_CONFIG_VERSION = 3
_FRAME_INDEX = 7


def _make_msg() -> SpectrumFrameMsg:
    return SpectrumFrameMsg(
        node_id="node_x",
        session_id=_SESSION_ID,
        stream_id=_STREAM_ID,
        config_version=_CONFIG_VERSION,
        frame_index=_FRAME_INDEX,
        timestamp_utc=_TIMESTAMP,
        payload=b"",  # raw bytes are passed alongside; this field unused by viewer encoder
    )


def _make_payload(value: float = -70.0) -> bytes:
    return struct.pack(f"<{_BIN_COUNT}f", *[value] * _BIN_COUNT)


def _encode() -> tuple[bytes, bytes]:
    payload = _make_payload()
    encoded = encode_viewer_spectrum_frame_binary(_AGENT_ID, _SESSION_ID, _make_msg(), payload)
    return encoded, payload


def test_returns_bytes() -> None:
    encoded, _ = _encode()
    assert isinstance(encoded, bytes)


def test_header_length_prefix_is_correct() -> None:
    encoded, _ = _encode()
    declared_len = struct.unpack(">H", encoded[:2])[0]
    header = json.loads(encoded[2 : 2 + declared_len])
    assert isinstance(header, dict)


def test_header_fields_match_input() -> None:
    encoded, _ = _encode()
    header_len = struct.unpack(">H", encoded[:2])[0]
    header = json.loads(encoded[2 : 2 + header_len])
    assert header["msg_type"] == "spectrum_frame"
    assert header["agent_id"] == _AGENT_ID
    assert header["session_id"] == _SESSION_ID
    assert header["stream_id"] == _STREAM_ID
    assert header["config_version"] == _CONFIG_VERSION
    assert header["frame_index"] == _FRAME_INDEX
    assert header["timestamp_utc"] == _TIMESTAMP
    assert header["bin_count"] == _BIN_COUNT
    # Payload must NOT be inside the header — it lives after the header bytes
    assert "payload" not in header
    assert "data" not in header


def test_payload_bytes_match_input_unchanged() -> None:
    encoded, payload = _encode()
    header_len = struct.unpack(">H", encoded[:2])[0]
    raw_payload = encoded[2 + header_len :]
    assert raw_payload == payload


def test_payload_starts_on_4_byte_boundary() -> None:
    """Header is padded so browsers can build a Float32Array view without copying."""
    encoded, _ = _encode()
    header_len = struct.unpack(">H", encoded[:2])[0]
    payload_offset = 2 + header_len
    assert payload_offset % 4 == 0


def test_padded_header_still_parses_as_json() -> None:
    """Padding bytes (trailing spaces) must not break JSON parsing of the declared header slice."""
    encoded, _ = _encode()
    header_len = struct.unpack(">H", encoded[:2])[0]
    header = json.loads(encoded[2 : 2 + header_len])
    assert header["msg_type"] == "spectrum_frame"


def test_decoded_floats_match_input_value() -> None:
    encoded, _ = _encode()
    header_len = struct.unpack(">H", encoded[:2])[0]
    raw_payload = encoded[2 + header_len :]
    floats = struct.unpack(f"<{_BIN_COUNT}f", raw_payload)
    assert all(abs(v - -70.0) < 1e-6 for v in floats)


def test_rejects_header_longer_than_uint16_prefix() -> None:
    oversized_stream_id = "x" * 70000
    msg = SpectrumFrameMsg(
        node_id="node_x",
        session_id=_SESSION_ID,
        stream_id=oversized_stream_id,
        config_version=_CONFIG_VERSION,
        frame_index=_FRAME_INDEX,
        timestamp_utc=_TIMESTAMP,
        payload=b"",
    )

    try:
        encode_viewer_spectrum_frame_binary(_AGENT_ID, _SESSION_ID, msg, _make_payload())
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert exc.fatal is False
        assert "header length" in exc.message
    else:
        raise AssertionError("expected ProtocolError for oversized viewer frame header")


# ---------------------------------------------------------------------------
# Agent binary spectrum_frame decoder
# ---------------------------------------------------------------------------


def _build_agent_binary_frame(
    *,
    msg_type: str = "spectrum_frame",
    node_id: str = "node_x",
    session_id: str = _SESSION_ID,
    stream_id: str = _STREAM_ID,
    config_version: int = _CONFIG_VERSION,
    frame_index: int = _FRAME_INDEX,
    timestamp_utc: str = _TIMESTAMP,
    bin_count: int = _BIN_COUNT,
    payload: bytes | None = None,
    omit_field: str | None = None,
) -> bytes:
    header: dict = {
        "msg_type": msg_type,
        "node_id": node_id,
        "session_id": session_id,
        "stream_id": stream_id,
        "config_version": config_version,
        "frame_index": frame_index,
        "timestamp_utc": timestamp_utc,
        "bin_count": bin_count,
    }
    if omit_field is not None:
        header.pop(omit_field, None)
    header_bytes = json.dumps(header).encode("utf-8")
    if payload is None:
        payload = _make_payload()
    return struct.pack(">H", len(header_bytes)) + header_bytes + payload


def test_decode_spectrum_frame_binary_returns_msg_with_raw_payload() -> None:
    payload = _make_payload(value=-55.0)
    frame = _build_agent_binary_frame(payload=payload)
    msg = decode_spectrum_frame_binary(frame)
    assert msg.node_id == "node_x"
    assert msg.session_id == _SESSION_ID
    assert msg.stream_id == _STREAM_ID
    assert msg.config_version == _CONFIG_VERSION
    assert msg.frame_index == _FRAME_INDEX
    assert msg.timestamp_utc == _TIMESTAMP
    assert msg.payload == payload  # byte-for-byte raw float32 LE


def test_decode_binary_rejects_buffer_shorter_than_length_prefix() -> None:
    try:
        decode_spectrum_frame_binary(b"\x00")
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert exc.fatal is False
    else:
        raise AssertionError("expected ProtocolError for short buffer")


def test_decode_binary_rejects_header_len_overflowing_buffer() -> None:
    # Declare header_len = 999 but supply only 5 bytes of header.
    buf = struct.pack(">H", 999) + b"hello"
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "overflows" in exc.message
    else:
        raise AssertionError("expected ProtocolError for header_len overflow")


def test_decode_binary_rejects_zero_header_len() -> None:
    buf = struct.pack(">H", 0) + _make_payload()
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
    else:
        raise AssertionError("expected ProtocolError for zero header_len")


def test_decode_binary_rejects_non_json_header() -> None:
    bad_header = b"this is not json"
    buf = struct.pack(">H", len(bad_header)) + bad_header + _make_payload()
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "not valid JSON" in exc.message
    else:
        raise AssertionError("expected ProtocolError for non-JSON header")


def test_decode_binary_rejects_wrong_msg_type() -> None:
    buf = _build_agent_binary_frame(msg_type="heartbeat")
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "spectrum_frame" in exc.message
    else:
        raise AssertionError("expected ProtocolError for wrong msg_type")


def test_decode_binary_rejects_missing_required_field() -> None:
    buf = _build_agent_binary_frame(omit_field="session_id")
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "session_id" in exc.message
    else:
        raise AssertionError("expected ProtocolError for missing session_id")


def test_decode_binary_preserves_payload_byte_equality() -> None:
    """Bytes in == bytes out: no base64 round-trip, no copies that mutate."""
    raw = struct.pack(f"<{_BIN_COUNT}f", *[float(i) - 50.0 for i in range(_BIN_COUNT)])
    buf = _build_agent_binary_frame(payload=raw)
    msg = decode_spectrum_frame_binary(buf)
    assert msg.payload == raw
    floats = struct.unpack(f"<{_BIN_COUNT}f", msg.payload)
    assert all(abs(floats[i] - (float(i) - 50.0)) < 1e-6 for i in range(_BIN_COUNT))


def test_decode_binary_returns_bin_count_from_header() -> None:
    msg = decode_spectrum_frame_binary(_build_agent_binary_frame())
    assert msg.bin_count == _BIN_COUNT


def test_decode_binary_rejects_missing_bin_count() -> None:
    buf = _build_agent_binary_frame(omit_field="bin_count")
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "bin_count" in exc.message
    else:
        raise AssertionError("expected ProtocolError for missing bin_count")


def test_decode_binary_rejects_non_int_bin_count() -> None:
    # Header with bin_count as a string should fail validation.
    header = {
        "msg_type": "spectrum_frame",
        "node_id": "node_x",
        "session_id": _SESSION_ID,
        "stream_id": _STREAM_ID,
        "config_version": _CONFIG_VERSION,
        "frame_index": _FRAME_INDEX,
        "timestamp_utc": _TIMESTAMP,
        "bin_count": "16",
    }
    header_bytes = json.dumps(header).encode("utf-8")
    buf = struct.pack(">H", len(header_bytes)) + header_bytes + _make_payload()
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "bin_count" in exc.message
    else:
        raise AssertionError("expected ProtocolError for non-int bin_count")


def test_decode_binary_rejects_non_positive_bin_count() -> None:
    buf = _build_agent_binary_frame(bin_count=0, payload=b"")
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "bin_count" in exc.message
    else:
        raise AssertionError("expected ProtocolError for bin_count=0")


def test_decode_binary_rejects_payload_length_not_matching_header_bin_count() -> None:
    # Header advertises 16 bins but payload has only 8 floats.
    short_payload = struct.pack("<8f", *[-70.0] * 8)
    buf = _build_agent_binary_frame(bin_count=_BIN_COUNT, payload=short_payload)
    try:
        decode_spectrum_frame_binary(buf)
    except ProtocolError as exc:
        assert exc.code == "INVALID_FRAME"
        assert "payload length" in exc.message
    else:
        raise AssertionError("expected ProtocolError for header/payload length mismatch")
