"""Session interface — protocol lifecycle and state machine.

This is the brain. Owns the five-state machine, drives the handshake
sequence, gates frame flow, and coordinates with transport.

States: DISCONNECTED → CONNECTING → CONNECTED → CONFIGURED → STREAMING
        Any failure resets to DISCONNECTED.
"""

from __future__ import annotations

import asyncio
import datetime
from contextlib import suppress
from typing import Protocol

from agent.config import AgentConfig
from agent.domain import (
    ConnectionState,
    FFTSemantics,
    RFConfig,
    SpectrumFrame,
    WireEncoding,
)
from agent.protocol import (
    ConnectAck,
    Disconnect,
    ProtocolCodec,
    ServerError,
    StreamConfigAck,
)
from agent.transport import Transport

_PROTOCOL_VERSION = "0.3"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class SessionError(Exception):
    """Protocol-level session failure. Caller should reconnect."""


# ---------------------------------------------------------------------------
# Protocol stubs (kept for type-checking consumers)
# ---------------------------------------------------------------------------


class SessionEventHandler(Protocol):
    """Callbacks the session fires on state transitions."""

    async def on_state_change(
        self, old: ConnectionState, new: ConnectionState
    ) -> None: ...

    async def on_error(self, code: str, message: str, fatal: bool) -> None: ...


class SessionProtocol(Protocol):
    """Manages the agent-server protocol lifecycle."""

    @property
    def state(self) -> ConnectionState: ...

    @property
    def session_id(self) -> str | None: ...

    @property
    def config_version(self) -> int | None: ...

    async def run(self, frame_queue: asyncio.Queue[SpectrumFrame]) -> None: ...

    async def request_config_update(self, rf_config: RFConfig) -> None: ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------


class Session:
    """Single-run session state machine.

    Drives the protocol handshake and streams frames. No retries — if run()
    returns or raises, the caller is responsible for reconnecting.
    """

    def __init__(
        self,
        config: AgentConfig,
        transport: Transport,
        codec: ProtocolCodec,
    ) -> None:
        self._config = config
        self._transport = transport
        self._codec = codec

        self._state = ConnectionState.DISCONNECTED
        self._session_id: str | None = None
        self._config_version: int | None = None
        self._frame_index: int = 0
        self._config_ack_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Public state (read-only)
    # ------------------------------------------------------------------

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def config_version(self) -> int | None:
        return self._config_version

    @property
    def frame_index(self) -> int:
        return self._frame_index

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, frame_queue: asyncio.Queue[SpectrumFrame]) -> None:
        """Run the session until cancelled or a fatal error occurs.

        Sets state back to DISCONNECTED on exit (including cancellation).
        Raises SessionError on protocol-level failures.
        Raises asyncio.CancelledError if cancelled externally.
        """
        self._state = ConnectionState.CONNECTING
        try:
            await self._handshake()
            self._frame_index = 0
            self._state = ConnectionState.STREAMING
            await self._stream(frame_queue)
        finally:
            self._state = ConnectionState.DISCONNECTED
            self._session_id = None
            self._config_version = None
            self._frame_index = 0
            self._config_ack_event = None
            with suppress(Exception):
                await self._transport.close()

    async def _handshake(self) -> None:
        cfg = self._config

        await self._transport.connect(cfg.server.url, cfg.server.token)

        session_id = self._transport.session_id_from_header
        if not session_id:
            raise SessionError("No session_id in HTTP response header")
        self._session_id = session_id

        # Send connect
        await self._transport.send(
            self._codec.encode_connect(
                node_id=cfg.identity.node_id,
                protocol_version=_PROTOCOL_VERSION,
                agent_version=cfg.identity.agent_version,
                requested_encoding=WireEncoding.JSON_BASE64,
            )
        )

        # Receive connect_ack
        raw = await self._transport.recv()
        try:
            msg = self._codec.decode(raw)
        except Exception as exc:
            raise SessionError(f"Failed to decode connect_ack: {exc}") from exc
        if not isinstance(msg, ConnectAck):
            raise SessionError(f"Expected connect_ack, got {type(msg).__name__!r}")
        if msg.session_id != self._session_id:
            raise SessionError(
                f"connect_ack session_id mismatch: "
                f"expected {self._session_id!r}, got {msg.session_id!r}"
            )
        if msg.status != "ok":
            raise SessionError(f"connect_ack status not ok: {msg.status!r}")
        if msg.wire_encoding != WireEncoding.JSON_BASE64:
            raise SessionError(
                f"connect_ack wire_encoding mismatch: got {msg.wire_encoding!r}"
            )
        self._state = ConnectionState.CONNECTED

        # Send stream_config
        await self._transport.send(
            self._codec.encode_stream_config(
                node_id=cfg.identity.node_id,
                session_id=self._session_id,
                stream_id=cfg.stream_id,
                timestamp_utc=_utc_now(),
                rf_config=cfg.rf,
                fft_semantics=FFTSemantics(),
            )
        )

        # Receive stream_config_ack
        raw = await self._transport.recv()
        try:
            msg = self._codec.decode(raw)
        except Exception as exc:
            raise SessionError(f"Failed to decode stream_config_ack: {exc}") from exc
        if not isinstance(msg, StreamConfigAck):
            raise SessionError(
                f"Expected stream_config_ack, got {type(msg).__name__!r}"
            )
        if msg.session_id != self._session_id:
            raise SessionError("stream_config_ack session_id mismatch")
        if msg.stream_id != cfg.stream_id:
            raise SessionError(
                f"stream_config_ack stream_id mismatch: "
                f"expected {cfg.stream_id!r}, got {msg.stream_id!r}"
            )
        if msg.status != "ok":
            raise SessionError(f"stream_config_ack status not ok: {msg.status!r}")
        self._config_version = msg.config_version
        self._state = ConnectionState.CONFIGURED

    async def _stream(self, frame_queue: asyncio.Queue[SpectrumFrame]) -> None:
        send_task = asyncio.create_task(self._send_loop(frame_queue))
        recv_task = asyncio.create_task(self._recv_loop())

        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            with suppress(asyncio.CancelledError):
                await t
        for t in done:
            if (exc := t.exception()) is not None:
                raise exc

    async def _send_loop(self, frame_queue: asyncio.Queue[SpectrumFrame]) -> None:
        cfg = self._config
        # Both are guaranteed set after _handshake() completes.
        assert self._session_id is not None
        assert self._config_version is not None
        while True:
            frame = await frame_queue.get()
            encoded = self._codec.encode_spectrum_frame(
                node_id=cfg.identity.node_id,
                session_id=self._session_id,
                stream_id=cfg.stream_id,
                config_version=self._config_version,
                frame_index=self._frame_index,
                frame=frame,
            )
            try:
                await self._transport.send(encoded)
            except Exception as exc:
                raise SessionError(f"Transport send error: {exc}") from exc
            self._frame_index += 1

    async def _recv_loop(self) -> None:
        while True:
            try:
                raw = await self._transport.recv()
            except Exception as exc:
                raise SessionError(f"Transport error: {exc}") from exc

            try:
                msg = self._codec.decode(raw)
            except Exception:
                continue  # malformed message: skip

            if isinstance(msg, Disconnect):
                raise SessionError(f"Server disconnected: {msg.reason}")
            elif isinstance(msg, ServerError):
                if msg.fatal:
                    raise SessionError(f"Fatal server error: {msg.code}")
                # nonfatal: continue
            elif isinstance(msg, StreamConfigAck):
                # Validate before mutating any state — fail session on bad ack
                cfg = self._config
                if msg.session_id != self._session_id:
                    raise SessionError(
                        f"runtime stream_config_ack session_id mismatch: "
                        f"expected {self._session_id!r}, got {msg.session_id!r}"
                    )
                if msg.stream_id != cfg.stream_id:
                    raise SessionError(
                        f"runtime stream_config_ack stream_id mismatch: "
                        f"expected {cfg.stream_id!r}, got {msg.stream_id!r}"
                    )
                if msg.status != "ok":
                    raise SessionError(
                        f"runtime stream_config_ack status not ok: {msg.status!r}"
                    )
                self._config_version = msg.config_version
                self._frame_index = 0
                if self._config_ack_event is not None:
                    self._config_ack_event.set()
                    self._config_ack_event = None

    # ------------------------------------------------------------------
    # Config update (mid-session)
    # ------------------------------------------------------------------

    async def request_config_update(self, rf_config: RFConfig) -> None:
        """Send a new stream_config and wait for the server ack.

        Resets frame_index to 0 when the new config_version arrives.
        Only valid during STREAMING.
        """
        if self._state != ConnectionState.STREAMING:
            raise SessionError(
                f"Config update only valid during STREAMING, not {self._state.value!r}"
            )
        cfg = self._config
        assert self._session_id is not None  # set during handshake
        self._config_ack_event = asyncio.Event()
        await self._transport.send(
            self._codec.encode_stream_config(
                node_id=cfg.identity.node_id,
                session_id=self._session_id,
                stream_id=cfg.stream_id,
                timestamp_utc=_utc_now(),
                rf_config=rf_config,
                fft_semantics=FFTSemantics(),
            )
        )
        await self._config_ack_event.wait()
