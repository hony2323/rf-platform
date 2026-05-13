"""Protocol message codec interface.

Pure encode/decode. No state, no I/O.
Transforms between domain objects and wire-format bytes/dicts.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Protocol

from agent.domain import (
    AgentMetrics,
    FFTSemantics,
    HardwareInfo,
    RFConfig,
    SpectrumFrame,
    TunerConfig,
    WindowFunction,
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


@dataclass(frozen=True)
class ConfigRequest:
    """Server→agent push of a new RF/FFT/tuner config (protocol v0.5+)."""

    session_id: str
    stream_id: str
    request_id: str
    rf: RFConfig
    tuner: TunerConfig | None = None


InboundMessage = ConnectAck | StreamConfigAck | Disconnect | ServerError | ConfigRequest


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
        request_id: str | None = None,
    ) -> str:
        """Encode a `stream_config` message → JSON string.

        `request_id` is set when this stream_config is the agent's response to a
        server-pushed `config_request` (protocol v0.5+).
        """
        ...

    def encode_config_rejected(
        self,
        node_id: str,
        session_id: str,
        request_id: str,
        code: str,
        message: str,
    ) -> str:
        """Encode a `config_rejected` message → JSON string (v0.5+).

        Sent in response to a server `config_request` that the agent cannot
        honor (e.g. the source does not support live retune).
        """
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
# Validation helpers
# ---------------------------------------------------------------------------


def _require_str(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"'{field}' must be a string, got {type(value).__name__!r}")
    return value


def _require_int(value: Any, field: str) -> int:
    """Accept int only; reject bool (bool is a subclass of int in Python)."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"'{field}' must be an integer, got {type(value).__name__!r}")
    return value


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
        request_id: str | None = None,
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
        if request_id is not None:
            msg["request_id"] = request_id
        return json.dumps(msg)

    def encode_config_rejected(
        self,
        node_id: str,
        session_id: str,
        request_id: str,
        code: str,
        message: str,
    ) -> str:
        return json.dumps(
            {
                "msg_type": "config_rejected",
                "node_id": node_id,
                "session_id": session_id,
                "request_id": request_id,
                "code": code,
                "message": message,
            }
        )

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
                "parse_errors": metrics.drops.parse_errors,
            },
        }
        if metrics.pipeline is not None:
            p = metrics.pipeline
            msg["pipeline"] = {
                "parse_iq_p50_ms": p.parse_iq_p50_ms,
                "parse_iq_p99_ms": p.parse_iq_p99_ms,
                "fft_p50_ms": p.fft_p50_ms,
                "fft_p99_ms": p.fft_p99_ms,
                "encode_send_p50_ms": p.encode_send_p50_ms,
                "encode_send_p99_ms": p.encode_send_p99_ms,
                "iq_queue_depth_avg": p.iq_queue_depth_avg,
                "frame_queue_depth_avg": p.frame_queue_depth_avg,
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
            "config_request": self._decode_config_request,
        }
        handler = dispatch.get(msg_type)  # type: ignore[arg-type]
        if handler is None:
            raise ValueError(f"Unknown msg_type: {msg_type!r}")
        return handler(msg)

    def _decode_connect_ack(self, msg: dict[str, Any]) -> ConnectAck:
        try:
            session_id = _require_str(msg["session_id"], "session_id")
            status = _require_str(msg["status"], "status")
            wire_encoding_raw = msg["wire_encoding"]
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc
        try:
            wire_encoding = WireEncoding(wire_encoding_raw)
        except ValueError:
            raise ValueError(f"Unknown wire_encoding value: {wire_encoding_raw!r}")
        return ConnectAck(
            session_id=session_id,
            status=status,
            wire_encoding=wire_encoding,
        )

    def _decode_stream_config_ack(self, msg: dict[str, Any]) -> StreamConfigAck:
        try:
            session_id = _require_str(msg["session_id"], "session_id")
            stream_id = _require_str(msg["stream_id"], "stream_id")
            config_version = _require_int(msg["config_version"], "config_version")
            status = _require_str(msg["status"], "status")
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc
        return StreamConfigAck(
            session_id=session_id,
            stream_id=stream_id,
            config_version=config_version,
            status=status,
        )

    def _decode_disconnect(self, msg: dict[str, Any]) -> Disconnect:
        try:
            session_id = _require_str(msg["session_id"], "session_id")
            reason = _require_str(msg["reason"], "reason")
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc
        return Disconnect(session_id=session_id, reason=reason)

    def _decode_server_error(self, msg: dict[str, Any]) -> ServerError:
        try:
            session_id = _require_str(msg["session_id"], "session_id")
            code = _require_str(msg["code"], "code")
            message = _require_str(msg["message"], "message")
            fatal = msg["fatal"]
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc

        if not isinstance(fatal, bool):
            raise ValueError(f"'fatal' must be a boolean, got {type(fatal).__name__!r}")

        stream_id: str | None = None
        if (stream_id_raw := msg.get("stream_id")) is not None:
            stream_id = _require_str(stream_id_raw, "stream_id")

        config_version: int | None = None
        if (config_version_raw := msg.get("config_version")) is not None:
            config_version = _require_int(config_version_raw, "config_version")

        frame_index: int | None = None
        if (frame_index_raw := msg.get("frame_index")) is not None:
            frame_index = _require_int(frame_index_raw, "frame_index")

        return ServerError(
            session_id=session_id,
            code=code,
            message=message,
            fatal=fatal,
            stream_id=stream_id,
            config_version=config_version,
            frame_index=frame_index,
        )

    def _decode_config_request(self, msg: dict[str, Any]) -> ConfigRequest:
        try:
            session_id = _require_str(msg["session_id"], "session_id")
            stream_id = _require_str(msg["stream_id"], "stream_id")
            request_id = _require_str(msg["request_id"], "request_id")
            rf_dict = msg["rf"]
        except KeyError as exc:
            raise ValueError(f"Missing required field: {exc}") from exc

        if not isinstance(rf_dict, dict):
            raise ValueError("'rf' must be an object")

        try:
            center_freq_hz = _require_int(
                rf_dict["center_freq_hz"], "rf.center_freq_hz"
            )
            sample_rate_hz = _require_int(
                rf_dict["sample_rate_hz"], "rf.sample_rate_hz"
            )
            fft_size = _require_int(rf_dict["fft_size"], "rf.fft_size")
        except KeyError as exc:
            raise ValueError(f"Missing required rf field: {exc}") from exc

        window_fn_raw = rf_dict.get("window_fn", "hann")
        try:
            window_fn = WindowFunction(window_fn_raw)
        except ValueError:
            raise ValueError(f"Unknown window_fn value: {window_fn_raw!r}")

        rf = RFConfig(
            center_freq_hz=center_freq_hz,
            sample_rate_hz=sample_rate_hz,
            fft_size=fft_size,
            window_fn=window_fn,
        )

        tuner: TunerConfig | None = None
        if (tuner_dict := msg.get("tuner")) is not None:
            if not isinstance(tuner_dict, dict):
                raise ValueError("'tuner' must be an object")
            gain_db_raw = tuner_dict.get("gain_db")
            gain_db: float | None
            if gain_db_raw is None:
                gain_db = None
            elif isinstance(gain_db_raw, bool) or not isinstance(
                gain_db_raw, (int, float)
            ):
                raise ValueError(
                    "'tuner.gain_db' must be a number or null, got "
                    f"{type(gain_db_raw).__name__!r}"
                )
            else:
                gain_db = float(gain_db_raw)
            agc_raw = tuner_dict.get("agc", True)
            if not isinstance(agc_raw, bool):
                raise ValueError(
                    f"'tuner.agc' must be a boolean, got {type(agc_raw).__name__!r}"
                )
            tuner = TunerConfig(gain_db=gain_db, agc=agc_raw)

        return ConfigRequest(
            session_id=session_id,
            stream_id=stream_id,
            request_id=request_id,
            rf=rf,
            tuner=tuner,
        )


# ---------------------------------------------------------------------------
# binary_ws frame encoder (standalone helper — not part of ProtocolCodec)
# ---------------------------------------------------------------------------


def encode_spectrum_frame_binary_ws(
    node_id: str,
    session_id: str,
    stream_id: str,
    config_version: int,
    frame_index: int,
    frame: SpectrumFrame,
) -> bytes:
    """Encode a spectrum_frame as a binary WebSocket message.

    Layout: [uint16_be header_len][header_json_utf8][raw payload bytes]

    The header JSON carries all metadata; payload bytes are appended raw
    (not base64). Control messages are always JSON text; only
    spectrum_frame uses this binary path.
    """
    header: dict[str, Any] = {
        "msg_type": "spectrum_frame",
        "node_id": node_id,
        "session_id": session_id,
        "stream_id": stream_id,
        "config_version": config_version,
        "frame_index": frame_index,
        "timestamp_utc": frame.timestamp_utc,
        "bin_count": frame.bin_count,
    }
    header_bytes = json.dumps(header).encode("utf-8")
    return struct.pack(">H", len(header_bytes)) + header_bytes + frame.payload
