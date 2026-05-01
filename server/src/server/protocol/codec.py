from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from typing import Any

SUPPORTED_PROTOCOL_VERSION = "0.3"
SUPPORTED_ENCODINGS: tuple[str, ...] = ("json_base64", "binary_ws")
SUPPORTED_ENCODING = "json_base64"  # default when client did not request one
_VIEWER_HEADER_LEN_MAX = 0xFFFF
_AGENT_HEADER_LEN_MAX = 0xFFFF


class ProtocolError(Exception):
    def __init__(self, code: str, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fatal = fatal


@dataclass
class ConnectMsg:
    protocol_version: str
    node_id: str
    agent_version: str
    requested_encoding: str
    hardware: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamConfigMsg:
    node_id: str
    session_id: str
    stream_id: str
    timestamp_utc: str
    rf: dict[str, Any]
    fft_semantics: dict[str, Any]


@dataclass
class HeartbeatMsg:
    node_id: str
    session_id: str
    timestamp_utc: str


@dataclass
class AgentStatusMsg:
    node_id: str
    session_id: str
    timestamp_utc: str
    raw: dict[str, Any]


@dataclass
class SpectrumFrameMsg:
    node_id: str
    session_id: str
    stream_id: str
    config_version: int
    frame_index: int
    timestamp_utc: str
    payload: bytes  # raw float32 LE bytes (already base64-decoded if JSON path)
    bin_count: int | None = None  # required by binary_ws header; absent on json_base64


InboundMsg = ConnectMsg | StreamConfigMsg | HeartbeatMsg | AgentStatusMsg | SpectrumFrameMsg


def decode_message(raw: str) -> InboundMsg:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("INVALID_FRAME", "malformed JSON", fatal=False) from exc

    msg_type = data.get("msg_type")
    try:
        if msg_type == "connect":
            return ConnectMsg(
                protocol_version=data["protocol_version"],
                node_id=data["node_id"],
                agent_version=data.get("agent_version", ""),
                requested_encoding=data.get("requested_encoding", SUPPORTED_ENCODING),
                hardware=data.get("hardware", {}),
            )
        if msg_type == "stream_config":
            return StreamConfigMsg(
                node_id=data["node_id"],
                session_id=data["session_id"],
                stream_id=data["stream_id"],
                timestamp_utc=data["timestamp_utc"],
                rf=data["rf"],
                fft_semantics=data["fft_semantics"],
            )
        if msg_type == "heartbeat":
            return HeartbeatMsg(
                node_id=data["node_id"],
                session_id=data["session_id"],
                timestamp_utc=data["timestamp_utc"],
            )
        if msg_type == "agent_status":
            return AgentStatusMsg(
                node_id=data["node_id"],
                session_id=data["session_id"],
                timestamp_utc=data["timestamp_utc"],
                raw=data,
            )
        if msg_type == "spectrum_frame":
            payload_b64 = data["data"]["payload"]
            try:
                payload_bytes = base64.b64decode(payload_b64, validate=True)
            except Exception as exc:
                raise ProtocolError(
                    "INVALID_FRAME", "payload is not valid base64", fatal=False
                ) from exc
            return SpectrumFrameMsg(
                node_id=data["node_id"],
                session_id=data["session_id"],
                stream_id=data["stream_id"],
                config_version=data["config_version"],
                frame_index=data["frame_index"],
                timestamp_utc=data["timestamp_utc"],
                payload=payload_bytes,
            )
    except KeyError as exc:
        raise ProtocolError("INVALID_FRAME", f"missing field: {exc}", fatal=False) from exc

    raise ProtocolError("INVALID_FRAME", f"unknown msg_type: {msg_type!r}", fatal=False)


def decode_spectrum_frame_binary(buf: bytes) -> SpectrumFrameMsg:
    """Decode an agent → server binary spectrum_frame.

    Wire layout: ``[uint16_be header_len][header_json_utf8 (padded)][raw float32 LE]``

    Returns a :class:`SpectrumFrameMsg` with ``payload`` set to the raw payload
    bytes (no base64 decode). Raises :class:`ProtocolError` with code
    ``INVALID_FRAME`` on any parse failure.
    """
    if len(buf) < 2:
        raise ProtocolError(
            "INVALID_FRAME", "binary frame shorter than header length prefix", fatal=False
        )
    header_len = struct.unpack(">H", buf[:2])[0]
    if header_len == 0 or 2 + header_len > len(buf):
        raise ProtocolError(
            "INVALID_FRAME",
            f"binary frame header_len {header_len} overflows buffer of {len(buf)} bytes",
            fatal=False,
        )
    try:
        header = json.loads(buf[2 : 2 + header_len])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProtocolError(
            "INVALID_FRAME", f"binary frame header not valid JSON: {exc}", fatal=False
        ) from exc
    if not isinstance(header, dict):
        raise ProtocolError("INVALID_FRAME", "binary frame header is not an object", fatal=False)
    if header.get("msg_type") != "spectrum_frame":
        raise ProtocolError(
            "INVALID_FRAME",
            f"binary frame msg_type {header.get('msg_type')!r} != 'spectrum_frame'",
            fatal=False,
        )
    payload = buf[2 + header_len :]
    try:
        bin_count_raw = header["bin_count"]
        msg = SpectrumFrameMsg(
            node_id=header["node_id"],
            session_id=header["session_id"],
            stream_id=header["stream_id"],
            config_version=header["config_version"],
            frame_index=header["frame_index"],
            timestamp_utc=header["timestamp_utc"],
            payload=payload,
            bin_count=bin_count_raw,
        )
    except KeyError as exc:
        raise ProtocolError(
            "INVALID_FRAME", f"binary frame header missing field: {exc}", fatal=False
        ) from exc
    if not isinstance(bin_count_raw, int) or isinstance(bin_count_raw, bool) or bin_count_raw <= 0:
        raise ProtocolError(
            "INVALID_FRAME",
            f"binary frame header bin_count must be a positive int, got {bin_count_raw!r}",
            fatal=False,
        )
    expected_len = bin_count_raw * 4
    if len(payload) != expected_len:
        raise ProtocolError(
            "INVALID_FRAME",
            f"binary frame payload length {len(payload)} != bin_count*4 ({expected_len})",
            fatal=False,
        )
    return msg


# ---------------------------------------------------------------------------
# Outbound encoders
# ---------------------------------------------------------------------------


def encode_connect_ack(session_id: str, wire_encoding: str = SUPPORTED_ENCODING) -> str:
    return json.dumps(
        {
            "msg_type": "connect_ack",
            "session_id": session_id,
            "status": "ok",
            "wire_encoding": wire_encoding,
        }
    )


def encode_stream_config_ack(session_id: str, stream_id: str, config_version: int) -> str:
    return json.dumps(
        {
            "msg_type": "stream_config_ack",
            "session_id": session_id,
            "stream_id": stream_id,
            "config_version": config_version,
            "status": "ok",
        }
    )


def encode_error(
    session_id: str,
    code: str,
    message: str,
    fatal: bool,
    *,
    stream_id: str | None = None,
    config_version: int | None = None,
    frame_index: int | None = None,
) -> str:
    msg: dict[str, Any] = {
        "msg_type": "error",
        "session_id": session_id,
        "code": code,
        "message": message,
        "fatal": fatal,
    }
    if stream_id is not None:
        msg["stream_id"] = stream_id
    if config_version is not None:
        msg["config_version"] = config_version
    if frame_index is not None:
        msg["frame_index"] = frame_index
    return json.dumps(msg)


def encode_disconnect(session_id: str, reason: str) -> str:
    return json.dumps(
        {
            "msg_type": "disconnect",
            "session_id": session_id,
            "reason": reason,
        }
    )


# ---------------------------------------------------------------------------
# Viewer outbound encoders
# ---------------------------------------------------------------------------


def encode_viewer_subscribe_ack(agent_id: str, session_id: str, stream_id: str) -> str:
    return json.dumps(
        {
            "msg_type": "subscribe_ack",
            "agent_id": agent_id,
            "session_id": session_id,
            "stream_id": stream_id,
            "status": "ok",
        }
    )


def encode_viewer_stream_config(agent_id: str, session_id: str, config: dict) -> str:
    return json.dumps(
        {
            "msg_type": "stream_config",
            "agent_id": agent_id,
            "session_id": session_id,
            "stream_id": config["stream_id"],
            "config_version": config["config_version"],
            "rf": config["rf"],
            "fft_semantics": config["fft_semantics"],
        }
    )


def encode_viewer_spectrum_frame_binary(
    agent_id: str,
    session_id: str,
    msg: SpectrumFrameMsg,
    payload_bytes: bytes,
) -> bytes:
    """Encode a spectrum_frame as a binary WebSocket message for viewers.

    Layout: [uint16_be header_len][header_json_utf8 padded][raw float32 LE payload]

    Header is padded with spaces so the payload starts on a 4-byte boundary,
    letting browsers construct a Float32Array view without copying.
    """
    header = {
        "msg_type": "spectrum_frame",
        "agent_id": agent_id,
        "session_id": session_id,
        "stream_id": msg.stream_id,
        "config_version": msg.config_version,
        "frame_index": msg.frame_index,
        "timestamp_utc": msg.timestamp_utc,
        "bin_count": len(payload_bytes) // 4,
    }
    header_bytes = json.dumps(header).encode("utf-8")
    pad = (-(2 + len(header_bytes))) % 4
    header_bytes += b" " * pad
    if len(header_bytes) > _VIEWER_HEADER_LEN_MAX:
        raise ProtocolError(
            "INVALID_FRAME",
            f"viewer frame header length {len(header_bytes)} exceeds uint16 limit",
            fatal=False,
        )
    return struct.pack(">H", len(header_bytes)) + header_bytes + payload_bytes


def encode_viewer_error(code: str, message: str) -> str:
    return json.dumps(
        {
            "msg_type": "error",
            "code": code,
            "message": message,
        }
    )
