"""Protocol message codec interface.

Pure encode/decode. No state, no I/O.
Transforms between domain objects and wire-format bytes/dicts.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Protocol

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
        protocol_version: str,
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
        timestamp_utc: str,
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
        timestamp_utc: str,
    ) -> str:
        """Encode a `heartbeat` message → JSON string."""
        ...

    def encode_agent_status(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
        metrics: AgentMetrics,
    ) -> str:
        """Encode an `agent_status` message → JSON string."""
        ...

    def decode(self, raw: str | bytes) -> InboundMessage:
        """Decode a server message.

        Raises ValueError on unknown msg_type or malformed payload.
        """
        ...


# ---------------------------------------------------------------------------
# Concrete implementation — json_base64 encoding (MVP)
# ---------------------------------------------------------------------------


class JsonBase64Codec:
    """Stateless json_base64 codec. Encodes outbound, decodes inbound messages."""

    def encode_connect(
        self,
        node_id: str,
        protocol_version: str,
        agent_version: str,
        requested_encoding: WireEncoding,
        hardware: HardwareInfo | None = None,
    ) -> str:
        msg: dict[str, Any] = {
            "msg_type": "connect",
            "protocol_version": protocol_version,
            "node_id": node_id,
            "agent_version": agent_version,
            "requested_encoding": requested_encoding.value,
        }
        if hardware is not None:
            msg["hardware"] = {
                "vendor": hardware.vendor,
                "model": hardware.model,
                "serial": hardware.serial,
            }
        return json.dumps(msg)

    def encode_stream_config(
        self,
        node_id: str,
        session_id: str,
        stream_id: str,
        timestamp_utc: str,
        rf_config: RFConfig,
        fft_semantics: FFTSemantics,
    ) -> str:
        msg: dict[str, Any] = {
            "msg_type": "stream_config",
            "node_id": node_id,
            "session_id": session_id,
            "stream_id": stream_id,
            "timestamp_utc": timestamp_utc,
            "rf": {
                "center_freq_hz": rf_config.center_freq_hz,
                "sample_rate_hz": rf_config.sample_rate_hz,
                "fft_size": rf_config.fft_size,
                "baseband_start_hz": rf_config.baseband_start_hz,
                "baseband_end_hz": rf_config.baseband_end_hz,
                "bin_size_hz": rf_config.bin_size_hz,
                "bin_count": rf_config.effective_bin_count,
                "window_fn": rf_config.window_fn.value,
            },
            "fft_semantics": {
                "kind": fft_semantics.kind,
                "scale": fft_semantics.scale,
                "unit": fft_semantics.unit,
                "numeric_type": fft_semantics.numeric_type,
                "bin_order": fft_semantics.bin_order.value,
            },
        }
        return json.dumps(msg)

    def encode_spectrum_frame(
        self,
        node_id: str,
        session_id: str,
        stream_id: str,
        config_version: int,
        frame_index: int,
        frame: SpectrumFrame,
    ) -> str:
        msg: dict[str, Any] = {
            "msg_type": "spectrum_frame",
            "node_id": node_id,
            "session_id": session_id,
            "stream_id": stream_id,
            "config_version": config_version,
            "frame_index": frame_index,
            "timestamp_utc": frame.timestamp_utc,
            "data": {
                "payload": base64.b64encode(frame.payload).decode("ascii"),
            },
        }
        return json.dumps(msg)

    def encode_heartbeat(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
    ) -> str:
        msg: dict[str, Any] = {
            "msg_type": "heartbeat",
            "node_id": node_id,
            "session_id": session_id,
            "timestamp_utc": timestamp_utc,
        }
        return json.dumps(msg)

    def encode_agent_status(
        self,
        node_id: str,
        session_id: str,
        timestamp_utc: str,
        metrics: AgentMetrics,
    ) -> str:
        msg: dict[str, Any] = {
            "msg_type": "agent_status",
            "node_id": node_id,
            "session_id": session_id,
            "timestamp_utc": timestamp_utc,
            "cpu_usage_pct": metrics.cpu_usage_pct,
            "throttled": metrics.throttled,
            "tx_bytes_per_sec": metrics.tx_bytes_per_sec,
            "queue_depth": metrics.queue_depth,
            "queue_fill_pct": metrics.queue_fill_pct,
            "drops": {
                "local_throttle": metrics.drops.local_throttle,
                "queue_overflow": metrics.drops.queue_overflow,
                "server_rejected": metrics.drops.server_rejected,
            },
        }
        return json.dumps(msg)

    def decode(self, raw: str | bytes) -> InboundMessage:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        if not isinstance(msg, dict):
            raise ValueError("Expected JSON object")

        msg_type = msg.get("msg_type")
        dispatch: dict[str, Callable[[dict[str, Any]], InboundMessage]] = {
            "connect_ack": self._decode_connect_ack,
            "stream_config_ack": self._decode_stream_config_ack,
            "disconnect": self._decode_disconnect,
            "error": self._decode_server_error,
        }
        handler = dispatch.get(msg_type)  # type: ignore[arg-type]
        if handler is None:
            raise ValueError(f"Unknown msg_type: {msg_type!r}")
        return handler(msg)

    def _decode_connect_ack(self, msg: dict[str, Any]) -> ConnectAck:
        try:
            return ConnectAck(
                session_id=msg["session_id"],
                status=msg["status"],
                wire_encoding=WireEncoding(msg["wire_encoding"]),
            )
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc

    def _decode_stream_config_ack(self, msg: dict[str, Any]) -> StreamConfigAck:
        try:
            return StreamConfigAck(
                session_id=msg["session_id"],
                stream_id=msg["stream_id"],
                config_version=int(msg["config_version"]),
                status=msg["status"],
            )
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc

    def _decode_disconnect(self, msg: dict[str, Any]) -> Disconnect:
        try:
            return Disconnect(
                session_id=msg["session_id"],
                reason=msg["reason"],
            )
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc

    def _decode_server_error(self, msg: dict[str, Any]) -> ServerError:
        try:
            fatal = msg["fatal"]
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc

        if not isinstance(fatal, bool):
            raise ValueError(
                f"'fatal' must be a boolean, got {type(fatal).__name__!r}"
            )

        try:
            return ServerError(
                session_id=msg["session_id"],
                code=msg["code"],
                message=msg["message"],
                fatal=fatal,
                stream_id=msg.get("stream_id"),
                config_version=msg.get("config_version"),
                frame_index=msg.get("frame_index"),
            )
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc
