"""Wire-encoding bench for spectrum_frame.

Run:  pytest -m bench src/tests/bench/test_wire_encoding_bench.py -s

Measures, per (fft_size, encoding) cell:
  - wire bytes per frame
  - encode CPU p50/p99
  - decode CPU p50/p99
  - permessage-deflate ratio (zlib estimate, per-message, no context takeover)

And per (fft_size, encoding, deflate-on/off) cell:
  - localhost loopback latency p50/p99 (encode + ws.send + ws.recv ack + decode)

The bench produces a single printed table summarizing all cells. Results go
into docs/agent_wire_v0_4_plan.md "Measured baseline".

Payload generator uses uniform random log-power dBFS noise — worst case for
compression. Real spectra (noise floor + sparse peaks) compress strictly
better, so deflate ratios here are conservative.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import time
import zlib
from dataclasses import dataclass

import numpy as np
import pytest
import websockets

from agent.domain import SpectrumFrame
from agent.protocol import JsonBase64Codec, encode_spectrum_frame_binary_ws

pytestmark = pytest.mark.bench


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

ENCODE_ITERS = 500
LOOPBACK_ITERS = 200
FFT_SIZES = [1024, 4096, 16384, 131072]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(fft_size: int, seed: int = 0) -> SpectrumFrame:
    rng = np.random.default_rng(seed)
    samples = rng.uniform(-90.0, -50.0, size=fft_size).astype(np.float32)
    return SpectrumFrame(
        payload=samples.tobytes(),
        timestamp_utc="2026-01-01T00:00:00.000Z",
        bin_count=fft_size,
    )


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def _measure_us(fn, iters: int) -> tuple[float, float]:
    """Return (p50_us, p99_us) over `iters` calls of `fn`."""
    samples: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        t1 = time.perf_counter_ns()
        samples.append((t1 - t0) / 1000.0)
    samples.sort()
    return _percentile(samples, 0.50), _percentile(samples, 0.99)


def _encode_jb64(frame: SpectrumFrame, codec: JsonBase64Codec) -> str:
    return codec.encode_spectrum_frame(
        node_id="node_bench",
        session_id="ses_bench0123456789abcdef",
        stream_id="default",
        config_version=1,
        frame_index=0,
        frame=frame,
    )


def _encode_bin(frame: SpectrumFrame) -> bytes:
    return encode_spectrum_frame_binary_ws(
        node_id="node_bench",
        session_id="ses_bench0123456789abcdef",
        stream_id="default",
        config_version=1,
        frame_index=0,
        frame=frame,
    )


def _decode_jb64(wire: str) -> bytes:
    msg = json.loads(wire)
    return base64.b64decode(msg["data"]["payload"])


def _decode_bin(wire: bytes) -> bytes:
    header_len = struct.unpack(">H", wire[:2])[0]
    json.loads(wire[2 : 2 + header_len])
    return wire[2 + header_len :]


# ---------------------------------------------------------------------------
# Cell + table types
# ---------------------------------------------------------------------------


@dataclass
class EncodeCell:
    fft_size: int
    encoding: str
    wire_bytes: int
    encode_us_p50: float
    encode_us_p99: float
    decode_us_p50: float
    decode_us_p99: float
    deflate_ratio: float  # compressed / raw


@dataclass
class LoopbackCell:
    fft_size: int
    encoding: str
    deflate: bool
    ms_p50: float
    ms_p99: float


# ---------------------------------------------------------------------------
# Loopback bench (real localhost WS pair)
# ---------------------------------------------------------------------------


async def _loopback_one(
    fft_size: int,
    encoding: str,
    deflate: bool,
    iters: int,
) -> LoopbackCell:
    """Measure encode→ws.send→ws.recv-ack→decode round-trip latency."""
    frame = _make_frame(fft_size)
    codec = JsonBase64Codec()
    compression = "deflate" if deflate else None

    async def server_handler(ws):  # type: ignore[no-untyped-def]
        async for msg in ws:
            if isinstance(msg, str):
                _decode_jb64(msg)
            else:
                _decode_bin(msg)
            await ws.send(b"\x01")  # one-byte ack

    server = await websockets.serve(
        server_handler,
        host="127.0.0.1",
        port=0,
        compression=compression,
        max_size=2**26,  # 64 MiB to fit fft_size=131072 base64 frames
    )
    sock = next(iter(server.sockets))
    port = sock.getsockname()[1]

    samples_ms: list[float] = []
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}",
            compression=compression,
            max_size=2**26,
        ) as client:
            # Warm-up — first frame usually pays JIT/buffer-alloc cost
            wire = _encode_jb64(frame, codec) if encoding == "json_base64" else _encode_bin(frame)
            await client.send(wire)
            await client.recv()

            for _ in range(iters):
                t0 = time.perf_counter_ns()
                wire = (
                    _encode_jb64(frame, codec)
                    if encoding == "json_base64"
                    else _encode_bin(frame)
                )
                await client.send(wire)
                await client.recv()
                t1 = time.perf_counter_ns()
                samples_ms.append((t1 - t0) / 1_000_000.0)
    finally:
        server.close()
        await server.wait_closed()

    samples_ms.sort()
    return LoopbackCell(
        fft_size=fft_size,
        encoding=encoding,
        deflate=deflate,
        ms_p50=_percentile(samples_ms, 0.50),
        ms_p99=_percentile(samples_ms, 0.99),
    )


async def _run_all_loopbacks(fft_sizes: list[int], iters: int) -> list[LoopbackCell]:
    cells: list[LoopbackCell] = []
    for fft_size in fft_sizes:
        for encoding in ("json_base64", "binary_ws"):
            for deflate in (False, True):
                cells.append(await _loopback_one(fft_size, encoding, deflate, iters))
    return cells


# ---------------------------------------------------------------------------
# The bench test
# ---------------------------------------------------------------------------


def test_wire_encoding_bench(capsys: pytest.CaptureFixture[str]) -> None:
    codec = JsonBase64Codec()
    encode_cells: list[EncodeCell] = []

    for fft_size in FFT_SIZES:
        frame = _make_frame(fft_size)

        # json_base64
        wire_jb64 = _encode_jb64(frame, codec)
        wire_jb64_bytes = wire_jb64.encode("utf-8")
        ratio_jb64 = len(zlib.compress(wire_jb64_bytes)) / len(wire_jb64_bytes)
        enc_p50, enc_p99 = _measure_us(lambda: _encode_jb64(frame, codec), ENCODE_ITERS)
        dec_p50, dec_p99 = _measure_us(lambda: _decode_jb64(wire_jb64), ENCODE_ITERS)
        encode_cells.append(
            EncodeCell(
                fft_size=fft_size,
                encoding="json_base64",
                wire_bytes=len(wire_jb64_bytes),
                encode_us_p50=enc_p50,
                encode_us_p99=enc_p99,
                decode_us_p50=dec_p50,
                decode_us_p99=dec_p99,
                deflate_ratio=ratio_jb64,
            )
        )

        # binary_ws
        wire_bin = _encode_bin(frame)
        ratio_bin = len(zlib.compress(wire_bin)) / len(wire_bin)
        enc_p50, enc_p99 = _measure_us(lambda: _encode_bin(frame), ENCODE_ITERS)
        dec_p50, dec_p99 = _measure_us(lambda: _decode_bin(wire_bin), ENCODE_ITERS)
        encode_cells.append(
            EncodeCell(
                fft_size=fft_size,
                encoding="binary_ws",
                wire_bytes=len(wire_bin),
                encode_us_p50=enc_p50,
                encode_us_p99=enc_p99,
                decode_us_p50=dec_p50,
                decode_us_p99=dec_p99,
                deflate_ratio=ratio_bin,
            )
        )

    loopback_cells = asyncio.run(_run_all_loopbacks(FFT_SIZES, LOOPBACK_ITERS))

    with capsys.disabled():
        print()
        print("=" * 110)
        print("Encode/decode + wire size + deflate ratio (per-message zlib estimate)")
        print("=" * 110)
        print(
            f"{'fft_size':>9} {'encoding':>14} {'wire_bytes':>11} "
            f"{'enc µs p50':>11} {'enc µs p99':>11} "
            f"{'dec µs p50':>11} {'dec µs p99':>11} {'deflate':>8}"
        )
        print("-" * 110)
        for c in encode_cells:
            print(
                f"{c.fft_size:>9} {c.encoding:>14} {c.wire_bytes:>11} "
                f"{c.encode_us_p50:>11.1f} {c.encode_us_p99:>11.1f} "
                f"{c.decode_us_p50:>11.1f} {c.decode_us_p99:>11.1f} "
                f"{c.deflate_ratio:>8.3f}"
            )

        print()
        print("=" * 80)
        print("Loopback (localhost WS, encode + send + ack + decode)")
        print("=" * 80)
        print(
            f"{'fft_size':>9} {'encoding':>14} {'deflate':>8} "
            f"{'ms p50':>10} {'ms p99':>10}"
        )
        print("-" * 80)
        for c in loopback_cells:
            print(
                f"{c.fft_size:>9} {c.encoding:>14} {str(c.deflate):>8} "
                f"{c.ms_p50:>10.2f} {c.ms_p99:>10.2f}"
            )
        print()
