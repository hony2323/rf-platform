"""Helpers to build wired-up real agent components for integration tests.

All returned objects are the real production types — no fakes here.
The WebSocket layer is the only thing swapped out (via ws_connect injection).
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

from agent.config import AgentConfig, AgentIdentity, ServerConfig
from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    WireEncoding,
    WindowFunction,
)
from agent.protocol import JsonBase64Codec
from agent.session import Session
from agent.transport.transport import WebSocketTransport

_SERVER_URL = "ws://fake.local/ws"
_TOKEN = "test-token"
_NODE_ID = "integ-node-001"


def make_rf_config(fft_size: int = 1024) -> RFConfig:
    return RFConfig(
        center_freq_hz=433_920_000,
        sample_rate_hz=2_400_000,
        fft_size=fft_size,
        window_fn=WindowFunction.HANN,
    )


def make_iq_descriptor() -> IQDescriptor:
    """IQ descriptor whose sample_rate_hz and center_freq_hz match make_rf_config."""
    return IQDescriptor(
        sample_format=SampleFormat.FLOAT32,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=2_400_000,
        center_freq_hz=433_920_000,
    )


def make_agent_config(
    *,
    stream_id: str = "default",
    node_id: str = _NODE_ID,
    fft_size: int = 1024,
) -> AgentConfig:
    return AgentConfig(
        identity=AgentIdentity(node_id=node_id),
        server=ServerConfig(url=_SERVER_URL, token=_TOKEN),
        rf=make_rf_config(fft_size=fft_size),
        iq=make_iq_descriptor(),
        stream_id=stream_id,
        wire_encoding=WireEncoding.JSON_BASE64,
    )


def make_session(
    config: AgentConfig,
    ws_connect: Callable[..., Any],
) -> Session:
    """Wire up real Session + WebSocketTransport + JsonBase64Codec."""
    codec = JsonBase64Codec()
    transport = WebSocketTransport(ws_connect=ws_connect)
    return Session(config=config, transport=transport, codec=codec)


def make_iq_chunk(fft_size: int) -> bytes:
    """Return exactly fft_size float32 LE interleaved complex samples.

    Produces a constant-I, zero-Q signal (pure DC) which parse_iq will
    normalise to all-zeros after DC removal.  The specific values do not
    matter for integration tests — we only care that the pipeline emits
    exactly one SpectrumFrame of the right byte length.
    """
    n_floats = fft_size * 2
    return struct.pack(f"<{n_floats}f", *([0.5, 0.0] * fft_size))
