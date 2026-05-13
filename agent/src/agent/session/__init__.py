"""Session interface — protocol lifecycle and state machine.

This is the brain. Owns the five-state machine, drives the handshake
sequence, gates frame flow, and coordinates with transport.

States: DISCONNECTED → CONNECTING → CONNECTED → CONFIGURED → STREAMING
        Any failure resets to DISCONNECTED.
"""

from __future__ import annotations

import asyncio
import datetime
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent.telemetry.metrics import MetricsCollector
    from agent.telemetry.stage_timing import PipelineTiming

from agent.config import AgentConfig
from agent.domain import (
    ConnectionState,
    FFTSemantics,
    RFConfig,
    SpectrumFrame,
    WireEncoding,
)
from agent.processing import Processor
from agent.protocol import (
    ConfigRequest,
    ConnectAck,
    Disconnect,
    ProtocolCodec,
    ServerError,
    StreamConfigAck,
    encode_spectrum_frame_binary_ws,
)
from agent.session.bandwidth import make_limiter
from agent.source.base import IQSource, LiveRetunableSource
from agent.transport import Transport

_PROTOCOL_VERSION = "0.5"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class SessionError(Exception):
    """Protocol-level session failure. Caller should reconnect."""


class FatalSessionError(SessionError):
    """Server sent fatal=true. Do not reconnect — fix config first."""


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
        source: IQSource | None = None,
        processor: Processor | None = None,
        timings: PipelineTiming | None = None,
        metrics: MetricsCollector | None = None,
        on_connected: Callable[[], None] | None = None,
    ) -> None:
        self._config = config
        self._transport = transport
        self._codec = codec
        self._source = source
        self._processor = processor
        self._timings = timings
        self._metrics = metrics
        self._on_connected = on_connected

        self._state = ConnectionState.DISCONNECTED
        self._session_id: str | None = None
        self._config_version: int | None = None
        self._frame_index: int = 0
        self._config_ack_event: asyncio.Event | None = None
        self._wire_encoding: WireEncoding = WireEncoding.JSON_BASE64
        self._inbound_config_tasks: set[asyncio.Task[None]] = set()

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
            if self._on_connected is not None:
                self._on_connected()
            await self._stream(frame_queue)
        finally:
            self._state = ConnectionState.DISCONNECTED
            self._session_id = None
            self._config_version = None
            self._frame_index = 0
            self._config_ack_event = None
            self._wire_encoding = WireEncoding.JSON_BASE64
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
                requested_encoding=cfg.wire_encoding,
            )
        )

        # Receive connect_ack
        raw = await self._transport.recv()
        try:
            msg = self._codec.decode(raw)
        except Exception as exc:
            raise SessionError(f"Failed to decode connect_ack: {exc}") from exc
        if isinstance(msg, ServerError) and msg.fatal:
            raise FatalSessionError(
                f"Server rejected connection: {msg.code}: {msg.message}"
            )
        if not isinstance(msg, ConnectAck):
            raise SessionError(f"Expected connect_ack, got {type(msg).__name__!r}")
        if msg.session_id != self._session_id:
            raise SessionError(
                f"connect_ack session_id mismatch: "
                f"expected {self._session_id!r}, got {msg.session_id!r}"
            )
        if msg.status != "ok":
            raise SessionError(f"connect_ack status not ok: {msg.status!r}")
        if msg.wire_encoding != cfg.wire_encoding:
            raise SessionError(
                f"connect_ack wire_encoding mismatch: "
                f"expected {cfg.wire_encoding!r}, got {msg.wire_encoding!r}"
            )
        self._wire_encoding = msg.wire_encoding
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
        if isinstance(msg, ServerError) and msg.fatal:
            raise FatalSessionError(
                f"Server rejected stream config: {msg.code}: {msg.message}"
            )
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
        # Retrieve exceptions from ALL finished tasks before raising so that
        # asyncio does not log "Task exception was never retrieved" for any of
        # them (which happens when the exception is not fetched before GC).
        first_exc: BaseException | None = None
        for t in done:
            if not t.cancelled() and (task_exc := t.exception()) is not None:
                if first_exc is None:
                    first_exc = task_exc
        if first_exc is not None:
            raise first_exc

    async def _send_loop(self, frame_queue: asyncio.Queue[SpectrumFrame]) -> None:
        cfg = self._config
        # Both are guaranteed set after _handshake() completes.
        assert self._session_id is not None
        assert self._config_version is not None

        bw = cfg.bandwidth
        limiter = make_limiter(bw.max_bytes_per_sec, bw.strategy)
        frame_queue_max = cfg.queues.frame_queue_size

        while True:
            q_depth = frame_queue.qsize()
            if self._timings is not None:
                self._timings.record_frame_queue_depth(q_depth)
            if self._metrics is not None:
                self._metrics.set_queue_depth(q_depth)
                fill_pct = (
                    (q_depth / frame_queue_max * 100.0) if frame_queue_max > 0 else 0.0
                )
                self._metrics.set_queue_fill_pct(fill_pct)

            frame = await frame_queue.get()
            t_send = time.perf_counter()
            if self._wire_encoding == WireEncoding.BINARY_WS:
                wire: str | bytes = encode_spectrum_frame_binary_ws(
                    node_id=cfg.identity.node_id,
                    session_id=self._session_id,
                    stream_id=cfg.stream_id,
                    config_version=self._config_version,
                    frame_index=self._frame_index,
                    frame=frame,
                )
            else:
                wire = self._codec.encode_spectrum_frame(
                    node_id=cfg.identity.node_id,
                    session_id=self._session_id,
                    stream_id=cfg.stream_id,
                    config_version=self._config_version,
                    frame_index=self._frame_index,
                    frame=frame,
                )

            if limiter is not None:
                n = len(wire) if isinstance(wire, bytes) else len(wire.encode("utf-8"))
                if not limiter.should_send(n):
                    if self._metrics is not None:
                        self._metrics.inc_local_throttle()
                        self._metrics.set_throttled(True)
                    continue

            if self._metrics is not None:
                self._metrics.set_throttled(False)

            try:
                await self._transport.send(wire)
            except Exception as exc:
                raise SessionError(f"Transport send error: {exc}") from exc

            if self._metrics is not None:
                if isinstance(wire, bytes):
                    n_sent = len(wire)
                elif isinstance(wire, str):
                    n_sent = len(wire.encode("utf-8"))
                else:
                    n_sent = 0
                self._metrics.inc_tx_bytes(n_sent)

            if self._timings is not None:
                self._timings.record_encode_send_ms(
                    (time.perf_counter() - t_send) * 1000.0
                )
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
                    raise FatalSessionError(
                        f"Fatal server error: {msg.code}: {msg.message}"
                    )
                # Non-fatal frame rejection errors increment server_rejected.
                if msg.code in ("INVALID_FRAME", "FRAME_TOO_LARGE"):
                    if self._metrics is not None:
                        self._metrics.inc_server_rejected()
                # Other non-fatal errors: continue
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
            elif isinstance(msg, ConfigRequest):
                # Server is pushing a new config (initiated by a viewer).
                # Handle in a separate task so the recv_loop continues to
                # deliver the resulting stream_config_ack.
                task = asyncio.create_task(self._handle_config_request(msg))
                self._inbound_config_tasks.add(task)
                task.add_done_callback(self._inbound_config_tasks.discard)

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
        self._config_ack_event = asyncio.Event()
        await self._send_stream_config(rf_config)
        await self._config_ack_event.wait()

    async def _send_stream_config(
        self, rf_config: RFConfig, request_id: str | None = None
    ) -> None:
        """Encode and transmit a runtime stream_config message.

        Shared by both the public request_config_update path (no request_id)
        and the inbound config_request handler (echoes the server's request_id).
        """
        cfg = self._config
        assert self._session_id is not None  # set during handshake
        await self._transport.send(
            self._codec.encode_stream_config(
                node_id=cfg.identity.node_id,
                session_id=self._session_id,
                stream_id=cfg.stream_id,
                timestamp_utc=_utc_now(),
                rf_config=rf_config,
                fft_semantics=FFTSemantics(),
                request_id=request_id,
            )
        )

    async def _send_config_rejected(
        self, request_id: str, code: str, message: str
    ) -> None:
        cfg = self._config
        assert self._session_id is not None
        await self._transport.send(
            self._codec.encode_config_rejected(
                node_id=cfg.identity.node_id,
                session_id=self._session_id,
                request_id=request_id,
                code=code,
                message=message,
            )
        )

    async def _handle_config_request(self, msg: ConfigRequest) -> None:
        """Apply a server-pushed config_request to the running source/processor.

        On any failure, transmits a `config_rejected` carrying the request_id.
        On success, transmits a fresh `stream_config` carrying the request_id;
        the server's `stream_config_ack` flows back through `_recv_loop` as a
        normal runtime ack and updates `_config_version` / `_frame_index`.
        """
        cfg = self._config
        try:
            # Validate addressing
            if msg.session_id != self._session_id:
                await self._send_config_rejected(
                    msg.request_id,
                    "INVALID_FRAME",
                    f"session_id mismatch: expected {self._session_id!r}, "
                    f"got {msg.session_id!r}",
                )
                return
            if msg.stream_id != cfg.stream_id:
                await self._send_config_rejected(
                    msg.request_id,
                    "INVALID_FRAME",
                    f"stream_id mismatch: expected {cfg.stream_id!r}, "
                    f"got {msg.stream_id!r}",
                )
                return

            # Session must have been built with a source + processor (the
            # production runner always provides them; bare-bones unit-test
            # sessions may not).
            if self._source is None or self._processor is None:
                await self._send_config_rejected(
                    msg.request_id,
                    "CONFIG_REJECTED",
                    "agent does not support live config updates",
                )
                return

            # Source must support live retune
            if not isinstance(self._source, LiveRetunableSource):
                await self._send_config_rejected(
                    msg.request_id,
                    "CONFIG_REJECTED",
                    "source does not support live retune",
                )
                return

            # Apply to source first (hardware retune), then to FFT processor.
            try:
                await self._source.apply_rf_update(msg.rf, msg.tuner)
            except Exception as exc:
                await self._send_config_rejected(
                    msg.request_id,
                    "CONFIG_REJECTED",
                    f"source.apply_rf_update failed: {exc}",
                )
                return

            try:
                self._processor.configure(msg.rf)
            except Exception as exc:
                await self._send_config_rejected(
                    msg.request_id,
                    "CONFIG_REJECTED",
                    f"processor.configure failed: {exc}",
                )
                return

            # Emit the new stream_config carrying the request_id so the server
            # can correlate its in-flight tracking.
            await self._send_stream_config(msg.rf, request_id=msg.request_id)
        except Exception:
            # Last-resort: never let a handler crash propagate; transport may
            # already be closing if we got here.
            pass
