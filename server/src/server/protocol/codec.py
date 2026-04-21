from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SUPPORTED_PROTOCOL_VERSION = "0.3"
SUPPORTED_ENCODING = "json_base64"


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
    payload: str  # base64-encoded float32 LE


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
            return SpectrumFrameMsg(
                node_id=data["node_id"],
                session_id=data["session_id"],
                stream_id=data["stream_id"],
                config_version=data["config_version"],
                frame_index=data["frame_index"],
                timestamp_utc=data["timestamp_utc"],
                payload=data["data"]["payload"],
            )
    except KeyError as exc:
        raise ProtocolError("INVALID_FRAME", f"missing field: {exc}", fatal=False) from exc

    raise ProtocolError("INVALID_FRAME", f"unknown msg_type: {msg_type!r}", fatal=False)


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


def encode_viewer_spectrum_frame(agent_id: str, session_id: str, msg: SpectrumFrameMsg) -> str:
    return json.dumps(
        {
            "msg_type": "spectrum_frame",
            "agent_id": agent_id,
            "session_id": session_id,
            "stream_id": msg.stream_id,
            "config_version": msg.config_version,
            "frame_index": msg.frame_index,
            "timestamp_utc": msg.timestamp_utc,
            "data": {"payload": msg.payload},
        }
    )


def encode_viewer_error(code: str, message: str) -> str:
    return json.dumps(
        {
            "msg_type": "error",
            "code": code,
            "message": message,
        }
    )
