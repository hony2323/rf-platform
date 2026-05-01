from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.deps import get_db
from server.protocol.codec import (
    SUPPORTED_ENCODINGS,
    SUPPORTED_PROTOCOL_VERSION,
    AgentStatusMsg,
    ConnectMsg,
    HeartbeatMsg,
    ProtocolError,
    SpectrumFrameMsg,
    StreamConfigMsg,
    decode_message,
    decode_spectrum_frame_binary,
    encode_connect_ack,
    encode_error,
    encode_stream_config_ack,
    encode_viewer_spectrum_frame_binary,
    encode_viewer_stream_config,
)
from server.sessions.models import LiveAgentSession
from server.storage.repositories.agent_tokens import get_active_token_by_hash
from server.storage.repositories.agents import get_agent_by_id_unscoped

logger = logging.getLogger(__name__)
router = APIRouter()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _deny(websocket: WebSocket, status: int) -> None:
    await websocket.send({"type": "websocket.http.response.start", "status": status, "headers": []})
    await websocket.send({"type": "websocket.http.response.body", "body": b"", "more_body": False})


async def _send_fatal(websocket: WebSocket, session_id: str, code: str, message: str) -> None:
    await websocket.send_text(encode_error(session_id, code, message, fatal=True))
    await websocket.close()


def _check_node_id(actual: str, expected: str) -> str | None:
    if actual != expected:
        return f"node_id mismatch: expected {expected!r}"
    return None


def _check_session_id(actual: str, expected: str) -> str | None:
    if actual != expected:
        return "session_id does not match this connection"
    return None


@router.websocket("/ws/agent")
async def ws_agent(websocket: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    # --- Bearer auth at HTTP Upgrade (before accept) ---
    auth = websocket.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        await _deny(websocket, 401)
        return

    token_hash = _hash_token(auth.removeprefix("Bearer "))
    token_record = await get_active_token_by_hash(db, token_hash)
    if token_record is None:
        await _deny(websocket, 401)
        return

    agent = await get_agent_by_id_unscoped(db, token_record.agent_id)
    if agent is None:
        await _deny(websocket, 401)
        return

    logger.info("agent connecting agent_id=%s node_id=%s", agent.id, agent.stable_node_id)

    # --- Accept and issue session_id ---
    session_id = "ses_" + uuid.uuid4().hex
    registry = websocket.app.state.registry

    await websocket.accept(headers=[(b"x-session-id", session_id.encode())])

    session: LiveAgentSession | None = None
    config_version = 0

    try:
        # ---- expect: connect ----
        raw = await websocket.receive_text()
        try:
            msg = decode_message(raw)
        except ProtocolError as exc:
            await _send_fatal(websocket, session_id, exc.code, exc.message)
            return

        if not isinstance(msg, ConnectMsg):
            await _send_fatal(websocket, session_id, "INVALID_FRAME", "expected connect")
            return

        if msg.protocol_version != SUPPORTED_PROTOCOL_VERSION:
            await _send_fatal(
                websocket,
                session_id,
                "PROTOCOL_MISMATCH",
                f"server requires protocol {SUPPORTED_PROTOCOL_VERSION}",
            )
            return

        if msg.requested_encoding not in SUPPORTED_ENCODINGS:
            await _send_fatal(
                websocket,
                session_id,
                "UNSUPPORTED_ENCODING",
                f"server supports {SUPPORTED_ENCODINGS}, got {msg.requested_encoding!r}",
            )
            return

        wire_encoding = msg.requested_encoding

        if err := _check_node_id(msg.node_id, agent.stable_node_id):
            await _send_fatal(websocket, session_id, "INVALID_FRAME", err)
            return

        await websocket.send_text(encode_connect_ack(session_id, wire_encoding=wire_encoding))

        # ---- expect: stream_config ----
        raw = await websocket.receive_text()
        try:
            msg = decode_message(raw)
        except ProtocolError as exc:
            await _send_fatal(websocket, session_id, exc.code, exc.message)
            return

        if not isinstance(msg, StreamConfigMsg):
            await _send_fatal(websocket, session_id, "INVALID_FRAME", "expected stream_config")
            return

        if err := _check_node_id(msg.node_id, agent.stable_node_id):
            await _send_fatal(websocket, session_id, "INVALID_FRAME", err)
            return

        if err := _check_session_id(msg.session_id, session_id):
            await _send_fatal(websocket, session_id, "INVALID_FRAME", err)
            return

        try:
            bin_count = int(msg.rf["bin_count"])
        except (KeyError, TypeError, ValueError):
            await _send_fatal(
                websocket, session_id, "INVALID_FRAME", "stream_config missing rf.bin_count"
            )
            return

        config_version = 1
        stream_id = msg.stream_id
        config_cache = {
            "session_id": session_id,
            "stream_id": msg.stream_id,
            "rf": msg.rf,
            "fft_semantics": msg.fft_semantics,
            "config_version": config_version,
        }

        session = LiveAgentSession(
            session_id=session_id,
            agent_id=str(agent.id),
            user_id=str(agent.user_id),
            stream_id=stream_id,
            config_version=config_version,
            bin_count=bin_count,
            last_stream_config=config_cache,
            last_config_version=config_version,
            wire_encoding=wire_encoding,
        )

        await websocket.send_text(encode_stream_config_ack(session_id, stream_id, config_version))
        registry.add_session(session)
        logger.info("agent session started session_id=%s agent_id=%s", session_id, agent.id)

        # ---- frame / heartbeat / status loop ----
        while True:
            if wire_encoding == "binary_ws":
                event = await websocket.receive()
                if event["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(event.get("code", 1000))
                if (raw_bytes := event.get("bytes")) is not None:
                    try:
                        msg = decode_spectrum_frame_binary(raw_bytes)
                    except ProtocolError as exc:
                        await websocket.send_text(
                            encode_error(session_id, exc.code, exc.message, fatal=False)
                        )
                        continue
                else:
                    raw = event.get("text") or ""
                    try:
                        msg = decode_message(raw)
                    except ProtocolError as exc:
                        await websocket.send_text(
                            encode_error(session_id, exc.code, exc.message, fatal=False)
                        )
                        continue
                    if isinstance(msg, SpectrumFrameMsg):
                        await websocket.send_text(
                            encode_error(
                                session_id,
                                "INVALID_FRAME",
                                "spectrum_frame must be sent as binary in binary_ws mode",
                                fatal=False,
                            )
                        )
                        continue
            else:
                raw = await websocket.receive_text()
                try:
                    msg = decode_message(raw)
                except ProtocolError as exc:
                    await websocket.send_text(
                        encode_error(session_id, exc.code, exc.message, fatal=False)
                    )
                    continue

            if isinstance(msg, (HeartbeatMsg, AgentStatusMsg, StreamConfigMsg, SpectrumFrameMsg)):
                err = _check_node_id(msg.node_id, agent.stable_node_id) or _check_session_id(
                    msg.session_id, session_id
                )
                if err:
                    await websocket.send_text(
                        encode_error(session_id, "INVALID_FRAME", err, fatal=False)
                    )
                    continue

            if isinstance(msg, HeartbeatMsg):
                registry.update_heartbeat(session_id)
            elif isinstance(msg, AgentStatusMsg):
                registry.update_status(session_id, json.dumps(msg.raw))
            elif isinstance(msg, StreamConfigMsg):
                try:
                    new_bin_count = int(msg.rf["bin_count"])
                except (KeyError, TypeError, ValueError):
                    await websocket.send_text(
                        encode_error(
                            session_id,
                            "INVALID_FRAME",
                            "stream_config missing rf.bin_count",
                            fatal=False,
                        )
                    )
                    continue
                config_version += 1
                config_cache = {
                    "session_id": session_id,
                    "stream_id": msg.stream_id,
                    "rf": msg.rf,
                    "fft_semantics": msg.fft_semantics,
                    "config_version": config_version,
                }
                registry.update_stream_config(
                    session_id, msg.stream_id, new_bin_count, config_version, config_cache
                )
                viewer_cfg = encode_viewer_stream_config(str(agent.id), session_id, config_cache)
                for viewer in registry.get_viewers_for_session(session_id):
                    try:
                        viewer.send_queue.put_nowait(viewer_cfg)
                    except asyncio.QueueFull:
                        pass
                await websocket.send_text(
                    encode_stream_config_ack(session_id, msg.stream_id, config_version)
                )
            elif isinstance(msg, SpectrumFrameMsg):
                if (
                    msg.stream_id != session.stream_id
                    or msg.config_version != session.config_version
                ):
                    sv = session.config_version
                    got = msg.config_version
                    await websocket.send_text(
                        encode_error(
                            session_id,
                            "INVALID_FRAME",
                            f"expected stream_id={session.stream_id}, config_version={sv} "
                            f"but got stream_id={msg.stream_id}, config_version={got}",
                            fatal=False,
                            stream_id=msg.stream_id,
                            config_version=msg.config_version,
                            frame_index=msg.frame_index,
                        )
                    )
                    continue
                payload_bytes = msg.payload
                expected_len = session.bin_count * 4
                if len(payload_bytes) != expected_len:
                    await websocket.send_text(
                        encode_error(
                            session_id,
                            "INVALID_FRAME",
                            f"payload length {len(payload_bytes)} != {expected_len}",
                            fatal=False,
                            stream_id=msg.stream_id,
                            config_version=msg.config_version,
                            frame_index=msg.frame_index,
                        )
                    )
                    continue
                try:
                    outbound = encode_viewer_spectrum_frame_binary(
                        str(agent.id), session_id, msg, payload_bytes
                    )
                except ProtocolError as exc:
                    await websocket.send_text(
                        encode_error(
                            session_id,
                            exc.code,
                            exc.message,
                            fatal=exc.fatal,
                            stream_id=msg.stream_id,
                            config_version=msg.config_version,
                            frame_index=msg.frame_index,
                        )
                    )
                    continue
                for viewer in registry.get_viewers_for_session(session_id):
                    try:
                        viewer.send_queue.put_nowait(outbound)
                    except asyncio.QueueFull:
                        pass
            else:
                await websocket.send_text(
                    encode_error(
                        session_id,
                        "INVALID_FRAME",
                        "unexpected message type",
                        fatal=False,
                    )
                )

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "agent unexpected error session_id=%s agent_id=%s",
            session_id,
            getattr(agent, "id", "unknown"),
        )
        try:
            await websocket.send_text(
                encode_error(session_id, "INTERNAL_ERROR", "server fault", fatal=True)
            )
            await websocket.close()
        except Exception:
            pass
    finally:
        if session is not None:
            registry.remove_session(session_id)
            logger.info("agent session ended session_id=%s agent_id=%s", session_id, agent.id)
