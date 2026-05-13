from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from server.sessions.registry import SessionRegistry  # noqa: F401

from server.app.deps import get_db
from server.auth.browser_auth import read_session_cookie
from server.protocol.codec import (
    encode_config_request,
    encode_request_config_error,
    encode_viewer_error,
    encode_viewer_stream_config,
    encode_viewer_subscribe_ack,
)
from server.sessions.models import (
    LiveAgentSession,
    PendingConfigRequest,
    ViewerSubscription,
)
from server.storage.repositories import agents as agents_repo
from server.storage.repositories import users as users_repo

logger = logging.getLogger(__name__)
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


_VALID_SAMPLE_RATES: frozenset[int] = frozenset(
    {240_000, 1_024_000, 1_600_000, 2_400_000, 2_560_000}
)
_VALID_WINDOW_FNS: frozenset[str] = frozenset({"hann"})
_MIN_CENTER_FREQ_HZ = 1_000_000
_MAX_CENTER_FREQ_HZ = 2_700_000_000
_MIN_FFT_SIZE = 1024
_MAX_FFT_SIZE = 131072
_MIN_GAIN_DB = 0.0
_MAX_GAIN_DB = 49.6
_CONFIG_REQUEST_TIMEOUT_S = 5.0


def _is_power_of_two(n: int) -> bool:
    return n > 0 and (n & (n - 1)) == 0


def _validate_request_config(data: dict) -> tuple[dict, dict | None] | str:
    """Validate a viewer's `request_config` payload.

    Returns either a (rf_dict, tuner_dict_or_None) pair on success or a
    string explaining the validation failure.
    """
    rf = data.get("rf")
    if not isinstance(rf, dict):
        return "rf must be an object"

    try:
        center = rf["center_freq_hz"]
        rate = rf["sample_rate_hz"]
        fft_size = rf["fft_size"]
    except KeyError as exc:
        return f"missing rf field: {exc}"

    if not isinstance(center, int) or isinstance(center, bool):
        return "rf.center_freq_hz must be an integer"
    if not isinstance(rate, int) or isinstance(rate, bool):
        return "rf.sample_rate_hz must be an integer"
    if not isinstance(fft_size, int) or isinstance(fft_size, bool):
        return "rf.fft_size must be an integer"
    if not (_MIN_CENTER_FREQ_HZ <= center <= _MAX_CENTER_FREQ_HZ):
        return (
            f"rf.center_freq_hz out of range "
            f"[{_MIN_CENTER_FREQ_HZ}, {_MAX_CENTER_FREQ_HZ}]: {center}"
        )
    if rate not in _VALID_SAMPLE_RATES:
        return f"rf.sample_rate_hz {rate} not in {sorted(_VALID_SAMPLE_RATES)}"
    if not (_MIN_FFT_SIZE <= fft_size <= _MAX_FFT_SIZE) or not _is_power_of_two(fft_size):
        return (
            f"rf.fft_size must be a power of two in "
            f"[{_MIN_FFT_SIZE}, {_MAX_FFT_SIZE}]: {fft_size}"
        )
    window_fn = rf.get("window_fn", "hann")
    if window_fn not in _VALID_WINDOW_FNS:
        return f"rf.window_fn {window_fn!r} not in {sorted(_VALID_WINDOW_FNS)}"

    out_rf: dict = {
        "center_freq_hz": center,
        "sample_rate_hz": rate,
        "fft_size": fft_size,
        "window_fn": window_fn,
    }

    tuner_in = data.get("tuner")
    out_tuner: dict | None = None
    if tuner_in is not None:
        if not isinstance(tuner_in, dict):
            return "tuner must be an object or absent"
        gain_db = tuner_in.get("gain_db")
        if gain_db is not None:
            if isinstance(gain_db, bool) or not isinstance(gain_db, (int, float)):
                return "tuner.gain_db must be a number or null"
            if not (_MIN_GAIN_DB <= float(gain_db) <= _MAX_GAIN_DB):
                return (
                    f"tuner.gain_db out of range [{_MIN_GAIN_DB}, {_MAX_GAIN_DB}]: "
                    f"{gain_db}"
                )
            gain_db = float(gain_db)
        agc = tuner_in.get("agc", True)
        if not isinstance(agc, bool):
            return "tuner.agc must be a boolean"
        out_tuner = {"gain_db": gain_db, "agc": agc}

    return out_rf, out_tuner


async def _expire_pending_request(
    session: LiveAgentSession,
    server_request_id: str,
    registry: Any,  # noqa: ANN401 — circular if we import SessionRegistry
) -> None:
    """Background timer: after 5s, evict the pending entry and notify the viewer."""
    try:
        await asyncio.sleep(_CONFIG_REQUEST_TIMEOUT_S)
    except asyncio.CancelledError:
        return
    pending = session.pending_config_requests.pop(server_request_id, None)
    if pending is None:
        return
    viewer = registry.get_viewer(pending.subscription_id)
    if viewer is None:
        return
    try:
        viewer.send_queue.put_nowait(
            encode_request_config_error(
                pending.viewer_request_id,
                "CONFIG_TIMEOUT",
                "agent did not respond within 5 seconds",
            )
        )
    except asyncio.QueueFull:
        pass


async def _handle_request_config(
    websocket: WebSocket,
    viewer: ViewerSubscription,
    session: LiveAgentSession,
    registry: Any,  # noqa: ANN401
    data: dict,
) -> None:
    """Process a viewer-sent `request_config` message."""
    viewer_request_id = data.get("request_id")
    if not isinstance(viewer_request_id, str) or not viewer_request_id:
        await websocket.send_text(
            encode_viewer_error("INVALID_FRAME", "request_id missing or not a string")
        )
        return

    # Serialize: one in-flight per session.
    if session.pending_config_requests:
        await websocket.send_text(
            encode_request_config_error(
                viewer_request_id,
                "CONFIG_BUSY",
                "another config change is being applied",
            )
        )
        return

    result = _validate_request_config(data)
    if isinstance(result, str):
        await websocket.send_text(
            encode_request_config_error(viewer_request_id, "INVALID_FRAME", result)
        )
        return
    rf_dict, tuner_dict = result

    server_request_id = "req_" + uuid.uuid4().hex
    session.pending_config_requests[server_request_id] = PendingConfigRequest(
        server_request_id=server_request_id,
        viewer_request_id=viewer_request_id,
        subscription_id=viewer.subscription_id,
    )

    # Schedule the timeout watcher; it's self-cleaning on success.
    asyncio.create_task(_expire_pending_request(session, server_request_id, registry))

    payload = encode_config_request(
        session.session_id, session.stream_id, server_request_id, rf_dict, tuner_dict
    )
    try:
        session.agent_send_queue.put_nowait(payload)
    except asyncio.QueueFull:
        # Agent send pipe is saturated; surface as REJECTED so the viewer's
        # promise resolves rather than waiting for the timeout.
        session.pending_config_requests.pop(server_request_id, None)
        await websocket.send_text(
            encode_request_config_error(
                viewer_request_id,
                "CONFIG_REJECTED",
                "agent send queue full",
            )
        )


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
        logger.info(
            "viewer subscribed subscription_id=%s user_id=%s agent_id=%s",
            subscription_id,
            user.id,
            agent.id,
        )

        await websocket.send_text(
            encode_viewer_subscribe_ack(str(agent.id), session.session_id, session.stream_id)
        )

        # Config-first: send current stream config before any frames
        if session.last_stream_config is not None:
            await websocket.send_text(
                encode_viewer_stream_config(
                    str(agent.id), session.session_id, session.last_stream_config
                )
            )

        # ---- drain loop ----
        # Race three signals: outbound message, client disconnect / inbound
        # control msg, session eviction. Each task persists across iterations
        # until it actually fires — otherwise re-arming would cause silent
        # drops (a queued item could be picked up by a task we no longer
        # reference).
        recv_task = asyncio.create_task(websocket.receive())
        close_task = asyncio.create_task(viewer.closed.wait())
        send_task = asyncio.create_task(viewer.send_queue.get())
        while True:
            done, _ = await asyncio.wait(
                {recv_task, send_task, close_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Drain pending outbound items BEFORE processing a session-close
            # signal: a final error broadcast (e.g. drain of pending config
            # requests on agent disconnect) may be enqueued and the close
            # signal set at the same instant; flushing first ensures the
            # client sees the error.
            if send_task in done:
                item = send_task.result()
                try:
                    if isinstance(item, bytes):
                        await websocket.send_bytes(item)
                    else:
                        await websocket.send_text(item)
                except Exception:
                    recv_task.cancel()
                    close_task.cancel()
                    await asyncio.gather(recv_task, close_task, return_exceptions=True)
                    break
                send_task = asyncio.create_task(viewer.send_queue.get())
                continue
            if close_task in done:
                send_task.cancel()
                recv_task.cancel()
                await asyncio.gather(send_task, recv_task, return_exceptions=True)
                await _close_with_error(websocket, "AGENT_OFFLINE", "agent session ended")
                return
            if recv_task in done:
                event = recv_task.result()
                if event.get("type") == "websocket.disconnect":
                    send_task.cancel()
                    close_task.cancel()
                    await asyncio.gather(send_task, close_task, return_exceptions=True)
                    break
                # Inbound control message — parse and dispatch.
                raw_text = event.get("text") or ""
                try:
                    inbound = json.loads(raw_text)
                except json.JSONDecodeError:
                    await websocket.send_text(
                        encode_viewer_error("INVALID_FRAME", "malformed JSON")
                    )
                    inbound = None
                if isinstance(inbound, dict):
                    msg_type = inbound.get("msg_type")
                    if msg_type == "request_config":
                        await _handle_request_config(
                            websocket, viewer, session, registry, inbound
                        )
                    else:
                        await websocket.send_text(
                            encode_viewer_error(
                                "INVALID_FRAME",
                                f"unexpected msg_type: {msg_type!r}",
                            )
                        )
                recv_task = asyncio.create_task(websocket.receive())
                continue

    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception(
            "viewer unexpected error subscription_id=%s",
            viewer.subscription_id if viewer else "none",
        )
        try:
            await websocket.send_text(encode_viewer_error("INTERNAL_ERROR", "server fault"))
            await websocket.close()
        except Exception:
            pass
    finally:
        if viewer is not None:
            websocket.app.state.registry.remove_viewer(viewer.subscription_id)
            logger.info("viewer unsubscribed subscription_id=%s", viewer.subscription_id)
