"""
Fake WebSocket server for local agent integration testing.

Implements the wire protocol handshake (connect → connect_ack →
stream_config → stream_config_ack → streaming) against real
WebSocket connections.

NOT FOR PRODUCTION — local testing only.

Usage as a standalone script::

    python scripts/fake_server.py

Or imported from integration tests::

    from fake_server import FakeAgentServer, FakeServerConfig
    async with FakeAgentServer(FakeServerConfig(expected_token="tok")) as srv:
        print(srv.ws_url)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import websockets.asyncio.server as _ws_srv
from websockets.asyncio.server import ServerConnection
from websockets.http11 import Headers, Response

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class FakeServerConfig:
    """Behaviour knobs for FakeAgentServer.

    All fields default to safe/permissive values so that a bare
    ``FakeServerConfig()`` works for happy-path tests.
    """

    host: str = "127.0.0.1"
    port: int = 0  # 0 → OS-assigned ephemeral port

    # Authentication — None means accept any token
    expected_token: str | None = None

    # Error injection (frame counts are 1-based, inclusive)
    send_nonfatal_error_after_n_frames: int | None = None
    send_fatal_error_after_n_frames: int | None = None
    disconnect_after_n_frames: int | None = None

    # Artificial delays for handshake timing tests
    delay_connect_ack_s: float = 0.0
    delay_stream_config_ack_s: float = 0.0

    # Memory control — set False for throughput tests to avoid accumulating
    # hundreds of thousands of frame dicts in RAM.
    store_frames: bool = True


# ---------------------------------------------------------------------------
# Connection record
# ---------------------------------------------------------------------------


@dataclass
class ConnectionRecord:
    """In-memory log of one accepted agent connection."""

    auth_header: str | None
    session_id: str
    connect_msg: dict[str, Any] | None = None
    stream_config_msg: dict[str, Any] | None = None
    frames: list[dict[str, Any]] = field(default_factory=list)
    frame_count: int = 0  # total spectrum_frame messages received (always incremented)
    heartbeats: list[dict[str, Any]] = field(default_factory=list)
    statuses: list[dict[str, Any]] = field(default_factory=list)
    rx_bytes: int = 0  # total UTF-8 bytes received from the agent


# ---------------------------------------------------------------------------
# Internal per-connection state machine
# ---------------------------------------------------------------------------


class _State(Enum):
    AWAIT_CONNECT = "await_connect"
    AWAIT_STREAM_CONFIG = "await_stream_config"
    STREAMING = "streaming"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Protocol validation helpers (light — not a real server)
# ---------------------------------------------------------------------------


_KNOWN_ENCODINGS = {"json_base64", "binary_ws"}


def _validate_connect(msg: dict[str, Any]) -> str | None:
    """Return an error string on failure, None on success."""
    if msg.get("msg_type") != "connect":
        return f"expected msg_type=connect, got {msg.get('msg_type')!r}"
    if msg.get("protocol_version") != "0.3":
        return f"expected protocol_version=0.3, got {msg.get('protocol_version')!r}"
    if msg.get("requested_encoding") not in _KNOWN_ENCODINGS:
        return (
            f"unknown requested_encoding: {msg.get('requested_encoding')!r}; "
            f"expected one of {sorted(_KNOWN_ENCODINGS)}"
        )
    if not msg.get("node_id"):
        return "missing or empty node_id"
    return None


def _validate_stream_config(msg: dict[str, Any], session_id: str) -> str | None:
    if msg.get("msg_type") != "stream_config":
        return f"expected msg_type=stream_config, got {msg.get('msg_type')!r}"
    if msg.get("session_id") != session_id:
        return (
            f"session_id mismatch: expected {session_id!r}, "
            f"got {msg.get('session_id')!r}"
        )
    if not msg.get("stream_id"):
        return "missing or empty stream_id"
    if "rf" not in msg:
        return "missing rf section"
    if "fft_semantics" not in msg:
        return "missing fft_semantics section"
    return None


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


async def _send_error(
    websocket: ServerConnection,
    session_id: str,
    code: str,
    message: str,
    *,
    fatal: bool,
) -> None:
    await websocket.send(
        json.dumps(
            {
                "msg_type": "error",
                "session_id": session_id,
                "code": code,
                "message": message,
                "fatal": fatal,
            }
        )
    )


async def _send_disconnect(
    websocket: ServerConnection,
    session_id: str,
    reason: str = "server_requested",
) -> None:
    await websocket.send(
        json.dumps(
            {
                "msg_type": "disconnect",
                "session_id": session_id,
                "reason": reason,
            }
        )
    )


# ---------------------------------------------------------------------------
# FakeAgentServer
# ---------------------------------------------------------------------------


class FakeAgentServer:
    """Real TCP WebSocket server implementing the agent wire protocol.

    Designed for local integration tests only.  Runs the full handshake
    state machine, records every message it receives, and supports
    configurable error injection.

    Usage::

        async with FakeAgentServer(FakeServerConfig(expected_token="tok")) as srv:
            # srv.ws_url  →  "ws://127.0.0.1:<ephemeral-port>"
            # connect your agent here
            assert srv.connections[0].connect_msg is not None
    """

    def __init__(self, config: FakeServerConfig | None = None) -> None:
        self._config = config or FakeServerConfig()
        self._connections: list[ConnectionRecord] = []
        self._server: Any = None  # websockets Server object

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind and start accepting connections."""
        self._server = await _ws_srv.serve(
            self._handle_connection,
            self._config.host,
            self._config.port,
            process_request=self._process_request,
            process_response=self._process_response,
            ping_interval=None,  # disable keepalive pings in tests
            compression=None,  # disable permessage-deflate in tests
        )

    async def stop(self) -> None:
        """Stop accepting connections and close the server socket."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "FakeAgentServer":
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def port(self) -> int:
        """Actual bound port (useful when config.port was 0)."""
        assert self._server is not None, "Server not started"
        return self._server.sockets[0].getsockname()[1]  # type: ignore[index]

    @property
    def ws_url(self) -> str:
        """WebSocket URL to connect to, e.g. ``ws://127.0.0.1:8765``."""
        return f"ws://{self._config.host}:{self.port}"

    @property
    def connections(self) -> list[ConnectionRecord]:
        """All connection records, one per accepted agent connection."""
        return self._connections

    # ------------------------------------------------------------------
    # WebSocket handshake hooks
    # ------------------------------------------------------------------

    async def _process_request(
        self,
        connection: ServerConnection,
        request: Any,
    ) -> Response | None:
        """Check Authorization header; generate and stash session_id."""
        auth: str = request.headers.get("Authorization", "")

        if self._config.expected_token is not None:
            expected = f"Bearer {self._config.expected_token}"
            if auth != expected:
                return Response(
                    401,
                    "Unauthorized",
                    Headers(),
                    b"Unauthorized\n",
                )

        session_id = str(uuid.uuid4())
        # Stash on the connection object so _process_response and handler
        # can read the same value without a shared dict.
        connection._agent_session_id = session_id  # type: ignore[attr-defined]
        connection._agent_auth_header = auth  # type: ignore[attr-defined]
        return None  # proceed with WebSocket upgrade

    async def _process_response(
        self,
        connection: ServerConnection,
        request: Any,
        response: Response,
    ) -> Response | None:
        """Inject X-Session-Id into the 101 Switching Protocols response."""
        sid: str = getattr(connection, "_agent_session_id", "")
        response.headers["X-Session-Id"] = sid
        return response

    # ------------------------------------------------------------------
    # Per-connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        session_id: str = getattr(websocket, "_agent_session_id", str(uuid.uuid4()))
        auth_header: str | None = getattr(websocket, "_agent_auth_header", None)

        record = ConnectionRecord(auth_header=auth_header, session_id=session_id)
        self._connections.append(record)

        state = _State.AWAIT_CONNECT
        config_version = 1
        frame_count = 0
        sent_nonfatal = False
        negotiated_encoding = "json_base64"

        try:
            async for raw in websocket:
                # binary_ws spectrum frames arrive as bytes during STREAMING
                if isinstance(raw, bytes):
                    if state is _State.STREAMING:
                        record.rx_bytes += len(raw)
                        record.frame_count += 1
                        frame_count += 1

                        n_fatal = self._config.send_fatal_error_after_n_frames
                        n_disc = self._config.disconnect_after_n_frames

                        if n_fatal is not None and frame_count >= n_fatal:
                            await _send_error(
                                websocket,
                                session_id,
                                "STREAM_ERROR",
                                "test fatal error injection",
                                fatal=True,
                            )
                            return

                        if n_disc is not None and frame_count >= n_disc:
                            await _send_disconnect(websocket, session_id)
                            return
                    continue

                record.rx_bytes += len(raw.encode("utf-8"))

                try:
                    msg: dict[str, Any] = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Received invalid JSON, ignoring")
                    continue

                msg_type: str = msg.get("msg_type", "")

                # ---- AWAIT_CONNECT ----------------------------------------
                if state is _State.AWAIT_CONNECT:
                    err = _validate_connect(msg)
                    if err:
                        await _send_error(
                            websocket,
                            session_id,
                            "INVALID_CONNECT",
                            err,
                            fatal=True,
                        )
                        return

                    record.connect_msg = msg
                    negotiated_encoding = msg.get("requested_encoding", "json_base64")

                    if self._config.delay_connect_ack_s > 0:
                        await asyncio.sleep(self._config.delay_connect_ack_s)

                    await websocket.send(
                        json.dumps(
                            {
                                "msg_type": "connect_ack",
                                "session_id": session_id,
                                "status": "ok",
                                "wire_encoding": negotiated_encoding,
                            }
                        )
                    )
                    state = _State.AWAIT_STREAM_CONFIG

                # ---- AWAIT_STREAM_CONFIG -----------------------------------
                elif state is _State.AWAIT_STREAM_CONFIG:
                    err = _validate_stream_config(msg, session_id)
                    if err:
                        await _send_error(
                            websocket,
                            session_id,
                            "INVALID_STREAM_CONFIG",
                            err,
                            fatal=True,
                        )
                        return

                    record.stream_config_msg = msg

                    if self._config.delay_stream_config_ack_s > 0:
                        await asyncio.sleep(self._config.delay_stream_config_ack_s)

                    await websocket.send(
                        json.dumps(
                            {
                                "msg_type": "stream_config_ack",
                                "session_id": session_id,
                                "stream_id": msg.get("stream_id", "default"),
                                "config_version": config_version,
                                "status": "ok",
                            }
                        )
                    )
                    state = _State.STREAMING

                # ---- STREAMING --------------------------------------------
                elif state is _State.STREAMING:
                    if msg_type == "spectrum_frame":
                        record.frame_count += 1
                        frame_count += 1
                        if self._config.store_frames:
                            record.frames.append(msg)

                        n_fatal = self._config.send_fatal_error_after_n_frames
                        n_nonfatal = self._config.send_nonfatal_error_after_n_frames
                        n_disc = self._config.disconnect_after_n_frames

                        if n_fatal is not None and frame_count >= n_fatal:
                            await _send_error(
                                websocket,
                                session_id,
                                "STREAM_ERROR",
                                "test fatal error injection",
                                fatal=True,
                            )
                            return

                        if (
                            n_nonfatal is not None
                            and frame_count >= n_nonfatal
                            and not sent_nonfatal
                        ):
                            sent_nonfatal = True
                            await _send_error(
                                websocket,
                                session_id,
                                "STREAM_WARNING",
                                "test nonfatal error injection",
                                fatal=False,
                            )
                            # Continue — nonfatal means keep streaming

                        if n_disc is not None and frame_count >= n_disc:
                            await _send_disconnect(websocket, session_id)
                            return

                    elif msg_type == "heartbeat":
                        record.heartbeats.append(msg)

                    elif msg_type == "agent_status":
                        record.statuses.append(msg)

                    else:
                        logger.debug("Unexpected msg_type in STREAMING: %r", msg_type)

        except Exception:
            logger.debug("Connection handler exiting", exc_info=True)

        state = _State.CLOSED


# ---------------------------------------------------------------------------
# Standalone script entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.DEBUG)

    parser = argparse.ArgumentParser(description="Fake agent WebSocket server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--token", default=None, help="Expected bearer token (optional)"
    )
    args = parser.parse_args()

    cfg = FakeServerConfig(host=args.host, port=args.port, expected_token=args.token)

    async def _run() -> None:
        async with FakeAgentServer(cfg) as server:
            print(f"Fake server listening on {server.ws_url}")
            await asyncio.Future()  # run forever

    asyncio.run(_run())
