"""Builder functions for server → agent wire messages.

Returns JSON strings matching the wire format exactly.
Use these in integration tests to pre-load FakeServer's message queue.
"""

from __future__ import annotations

import json
from typing import Any


def connect_ack_msg(
    session_id: str,
    *,
    status: str = "ok",
    wire_encoding: str = "json_base64",
) -> str:
    return json.dumps(
        {
            "msg_type": "connect_ack",
            "session_id": session_id,
            "status": status,
            "wire_encoding": wire_encoding,
        }
    )


def stream_config_ack_msg(
    session_id: str,
    stream_id: str,
    config_version: int,
    *,
    status: str = "ok",
) -> str:
    return json.dumps(
        {
            "msg_type": "stream_config_ack",
            "session_id": session_id,
            "stream_id": stream_id,
            "config_version": config_version,
            "status": status,
        }
    )


def disconnect_msg(session_id: str, reason: str = "test_done") -> str:
    return json.dumps(
        {
            "msg_type": "disconnect",
            "session_id": session_id,
            "reason": reason,
        }
    )


def server_error_msg(
    session_id: str,
    code: str,
    message: str,
    *,
    fatal: bool,
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
