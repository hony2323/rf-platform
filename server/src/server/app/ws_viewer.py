from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from server.app.deps import get_db
from server.auth.browser_auth import read_session_cookie
from server.protocol.codec import (
    encode_viewer_error,
    encode_viewer_stream_config,
    encode_viewer_subscribe_ack,
)
from server.sessions.models import ViewerSubscription
from server.storage.repositories import agents as agents_repo
from server.storage.repositories import users as users_repo

router = APIRouter()


async def _deny(websocket: WebSocket, status: int) -> None:
    await websocket.send({"type": "websocket.http.response.start", "status": status, "headers": []})
    await websocket.send({"type": "websocket.http.response.body", "body": b"", "more_body": False})


async def _close_with_error(websocket: WebSocket, code: str, message: str) -> None:
    try:
        await websocket.send_text(encode_viewer_error(code, message))
        await websocket.close()
    except Exception:
        pass


@router.websocket("/ws/viewer")
async def ws_viewer(websocket: WebSocket, db: AsyncSession = Depends(get_db)) -> None:
    # --- Cookie auth at HTTP Upgrade (before accept) ---
    settings = websocket.app.state.settings
    cookie = websocket.cookies.get(settings.session_cookie_name)
    if cookie is None:
        await _deny(websocket, 401)
        return

    user_id = read_session_cookie(cookie, settings.session_secret)
    if user_id is None:
        await _deny(websocket, 401)
        return

    user = await users_repo.get_user_by_id(db, user_id)
    if user is None:
        await _deny(websocket, 401)
        return

    await websocket.accept()

    viewer: ViewerSubscription | None = None

    try:
        # ---- expect: subscribe ----
        raw = await websocket.receive_text()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await _close_with_error(websocket, "INVALID_FRAME", "malformed JSON")
            return

        if data.get("msg_type") != "subscribe":
            await _close_with_error(websocket, "INVALID_FRAME", "expected subscribe")
            return

        agent_id = data.get("agent_id", "")
        if not agent_id:
            await _close_with_error(websocket, "INVALID_FRAME", "missing agent_id")
            return

        # Ownership: only the agent's owner may subscribe
        agent = await agents_repo.get_agent_by_id(db, agent_id, str(user.id))
        if agent is None:
            await _close_with_error(websocket, "FORBIDDEN", "agent not found")
            return

        registry = websocket.app.state.registry
        session = registry.get_session_by_agent(str(agent.id))
        if session is None:
            await _close_with_error(websocket, "AGENT_OFFLINE", "agent has no active session")
            return

        subscription_id = "sub_" + uuid.uuid4().hex
        viewer = ViewerSubscription(
            subscription_id=subscription_id,
            user_id=str(user.id),
            agent_id=str(agent.id),
            session_id=session.session_id,
        )
        registry.add_viewer(viewer)
        logger.info("viewer subscribed subscription_id=%s user_id=%s agent_id=%s", subscription_id, user.id, agent.id)

        await websocket.send_text(
            encode_viewer_subscribe_ack(str(agent.id), session.session_id, session.stream_id)
        )

        # Config-first: send current stream config before any frames
        if session.last_stream_config is not None:
            await websocket.send_text(
                encode_viewer_stream_config(str(agent.id), session.session_id, session.last_stream_config)
            )

        # ---- drain loop ----
        # Race three signals: outbound message, client disconnect, session eviction.
        recv_task = asyncio.create_task(websocket.receive())
        close_task = asyncio.create_task(viewer.closed.wait())
        while True:
            send_task = asyncio.create_task(viewer.send_queue.get())
            done, _ = await asyncio.wait(
                {recv_task, send_task, close_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if close_task in done:
                send_task.cancel()
                recv_task.cancel()
                await asyncio.gather(send_task, recv_task, return_exceptions=True)
                await _close_with_error(websocket, "AGENT_OFFLINE", "agent session ended")
                return
            if recv_task in done:
                send_task.cancel()
                await asyncio.gather(send_task, return_exceptions=True)
                break
            text = send_task.result()
            try:
                await websocket.send_text(text)
            except Exception:
                recv_task.cancel()
                close_task.cancel()
                await asyncio.gather(recv_task, close_task, return_exceptions=True)
                break

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("viewer unexpected error subscription_id=%s", viewer.subscription_id if viewer else "none")
        try:
            await websocket.send_text(encode_viewer_error("INTERNAL_ERROR", "server fault"))
            await websocket.close()
        except Exception:
            pass
    finally:
        if viewer is not None:
            websocket.app.state.registry.remove_viewer(viewer.subscription_id)
            logger.info("viewer unsubscribed subscription_id=%s", viewer.subscription_id)
