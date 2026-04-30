"""Unit tests for server.protocol.codec.

Contract tests: assert on decoded values only, never on raw byte equality
or JSON key ordering.
"""

from __future__ import annotations

import json
import struct

from server.protocol.codec import (
    SpectrumFrameMsg,
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
        payload="",  # raw bytes are passed alongside, payload string is unused here
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
