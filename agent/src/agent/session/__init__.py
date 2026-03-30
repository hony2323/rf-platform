"""Session interface — protocol lifecycle and state machine.

This is the brain. Owns the five-state machine, drives the handshake
sequence, gates frame flow, and coordinates with transport.

States: DISCONNECTED → CONNECTING → CONNECTED → CONFIGURED → STREAMING
        Any failure resets to DISCONNECTED.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from agent.config import AgentConfig
from agent.domain import (
    ConnectionState,
    RFConfig,
    SpectrumFrame,
)
from agent.protocol import ProtocolCodec
from agent.transport import Transport


class SessionEventHandler(Protocol):
    """Callbacks the session fires on state transitions."""

    async def on_state_change(
        self, old: ConnectionState, new: ConnectionState
    ) -> None: ...

    async def on_error(self, code: str, message: str, fatal: bool) -> None: ...


class Session(Protocol):
    """Manages the agent-server protocol lifecycle."""


class SessionManager:
    def __init__(
        self,
        *,
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
        self._next_frame_index: int = 0

        self._pending_config_update = False
        self._closed = False

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
    def next_frame_index(self) -> int:
        return self._next_frame_index

    async def run(
        self,
        frame_queue: asyncio.Queue[SpectrumFrame],
    ) -> None:
        """Main session loop. Runs until cancelled.

        Responsibilities:
        1. Initiate connection via transport (DISCONNECTED → CONNECTING)
        2. On transport connect: send `connect` (→ CONNECTED on ack)
        3. Send `stream_config` (→ CONFIGURED on ack)
        4. Drain frame_queue, encode + send frames (STREAMING)
        5. Handle inbound messages (acks, errors, disconnect)
        6. On any failure: reset to DISCONNECTED, backoff, retry

        The session stamps each frame with session_id, stream_id,
        config_version, and an incrementing frame_index.
        """
        ...

    async def request_config_update(self, rf_config: RFConfig) -> None:
        """Request a config change mid-session.

        Sends a new stream_config. Transitions back to CONFIGURED
        while waiting for ack. frame_index resets on new config_version.
        """
        ...
