"""Protocol message codec interface.

Pure encode/decode. No state, no I/O.
Transforms between domain objects and wire-format bytes/dicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agent.domain import (
    AgentMetrics,
    FFTSemantics,
    HardwareInfo,
    RFConfig,
    SpectrumFrame,
    WireEncoding,
)

# ---------------------------------------------------------------------------
# Inbound message types (server → agent)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectAck:
    session_id: str
    status: str
    wire_encoding: WireEncoding


@dataclass(frozen=True)
class StreamConfigAck:
    session_id: str
    stream_id: str
    config_version: int
    status: str


@dataclass(frozen=True)
class Disconnect:
    session_id: str
    reason: str


@dataclass(frozen=True)
class ServerError:
    session_id: str
    code: str
    message: str
    fatal: bool
    stream_id: str | None = None
    config_version: int | None = None
    frame_index: int | None = None


InboundMessage = ConnectAck | StreamConfigAck | Disconnect | ServerError


# ---------------------------------------------------------------------------
# Codec interface
# ---------------------------------------------------------------------------


class ProtocolCodec(Protocol):
    """Encodes outbound messages, decodes inbound messages.

    MVP: json_base64 encoding only.
    """

    def encode_connect(
        self,
        node_id: str,
        agent_version: str,
        requested_encoding: WireEncoding,
        hardware: HardwareInfo | None = None,
    ) -> str:
        """Encode a `connect` message → JSON string."""
        ...

    def encode_stream_config(
        self,
        node_id: str,
        session_id: str,
        stream_id: str,
        rf_config: RFConfig,
        fft_semantics: FFTSemantics,
    ) -> str:
        """Encode a `stream_config` message → JSON string."""
        ...

    def encode_spectrum_frame(
        self,
        node_id: str,
        session_id: str,
        stream_id: str,
        config_version: int,
        frame_index: int,
        frame: SpectrumFrame,
    ) -> str:
        """Encode a `spectrum_frame` message → JSON string.

        Payload is base64-encoded in json_base64 mode.
        """
        ...

    def encode_heartbeat(
        self,
        node_id: str,
        session_id: str,
    ) -> str:
        """Encode a `heartbeat` message → JSON string."""
        ...

    def encode_agent_status(
        self,
        node_id: str,
        session_id: str,
        metrics: AgentMetrics,
    ) -> str:
        """Encode an `agent_status` message → JSON string."""
        ...

    def decode(self, raw: str | bytes) -> InboundMessage:
        """Decode a server message.

        Raises ValueError on unknown msg_type or malformed payload.
        """
        ...
