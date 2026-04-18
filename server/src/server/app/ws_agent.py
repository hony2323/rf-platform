from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.deps import get_db
from server.protocol.codec import (
    SUPPORTED_ENCODING,
    SUPPORTED_PROTOCOL_VERSION,
    AgentStatusMsg,
    ConnectMsg,
    HeartbeatMsg,
    ProtocolError,
    SpectrumFrameMsg,
    StreamConfigMsg,
    decode_message,
    encode_connect_ack,
    encode_error,
    encode_stream_config_ack,
)
from server.sessions.models import LiveAgentSession
from server.storage.repositories.agent_tokens import get_active_token_by_hash
from server.storage.repositories.agents import get_agent_by_id_unscoped

router = APIRouter()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _deny(websocket: WebSocket, status: int) -> None:
    await websocket.send({"type": "websocket.http.response.start", "status": status, "headers": []})
    await websocket.send({"type": "websocket.http.response.body", "body": b"", "more_body": False})


async def _send_fatal(websocket: WebSocket, session_id: str, code: str, message: str) -> None:
    await websocket.send_text(encode_error(session_id, code, message, fatal=True))
    await websocket.close()


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
                websocket, session_id, "PROTOCOL_MISMATCH",
                f"server requires protocol {SUPPORTED_PROTOCOL_VERSION}",
            )
            return

        if msg.requested_encoding != SUPPORTED_ENCODING:
            await _send_fatal(
                websocket, session_id, "UNSUPPORTED_ENCODING",
                f"server only supports {SUPPORTED_ENCODING}",
            )
            return

        await websocket.send_text(encode_connect_ack(session_id))

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

        config_version = 1
        stream_id = msg.stream_id

        session = LiveAgentSession(
            session_id=session_id,
            agent_id=str(agent.id),
            user_id=str(agent.user_id),
            stream_id=stream_id,
            config_version=config_version,
        )
        registry.add_session(session)

        await websocket.send_text(
            encode_stream_config_ack(session_id, stream_id, config_version)
        )

        # ---- frame / heartbeat / status loop ----
        while True:
            raw = await websocket.receive_text()
            try:
                msg = decode_message(raw)
            except ProtocolError as exc:
                await websocket.send_text(
                    encode_error(session_id, exc.code, exc.message, exc.fatal)
                )
                if exc.fatal:
                    await websocket.close()
                    return
                continue

            if isinstance(msg, HeartbeatMsg):
                registry.update_heartbeat(session_id)
            elif isinstance(msg, AgentStatusMsg):
                registry.update_status(session_id, json.dumps(msg.raw))
            elif isinstance(msg, StreamConfigMsg):
                config_version += 1
                registry.update_config_version(session_id, config_version)
                session.stream_id = msg.stream_id
                await websocket.send_text(
                    encode_stream_config_ack(session_id, msg.stream_id, config_version)
                )
            elif isinstance(msg, SpectrumFrameMsg):
                pass  # Phase 6: frame ingestion
            else:
                await websocket.send_text(
                    encode_error(session_id, "INVALID_FRAME", "unexpected message type", fatal=False)
                )

    except WebSocketDisconnect:
        pass
    except Exception:
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
