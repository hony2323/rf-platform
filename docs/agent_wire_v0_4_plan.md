# Agent wire v0.4 — optimization plan

**Status:** all phases shipped 2026-05-01 (bench, server + agent default flip + tests, contract doc, status docs).
**Date:** 2026-05-01
**Scope:** Agent → Server WebSocket frames only. Server → Viewer (already v0.4 binary) is untouched. Control plane (`connect`, `stream_config`, `agent_status`, `heartbeat`) stays JSON text.

---

## Why

Agent → Server `spectrum_frame` is currently base64 JSON:

- 33% wire-size overhead from base64 (`bin_count*5.33` bytes vs `bin_count*4`).
- Per-frame allocations on the agent: `numpy → bytes → b64encode → dict → json.dumps → utf-8 encode`.
- Server immediately base64-decodes, validates, then re-emits raw bytes to viewers — the base64 round-trip is pure waste in the relay path.

We are post-MVP enough to break the contract. The cheapest win is making the agent → server path symmetric with the already-shipped server → viewer v0.4 binary frame.

There is **no observed bandwidth pressure** today. This is preemptive optimization — so the bench has to *prove* the wins before we adopt the change.

---

## Wire format (proposed v0.4 agent contract)

Only `spectrum_frame` becomes binary. Everything else stays JSON text exactly as v0.3.

```
[uint16 big-endian header_len][header_json_utf8 padded][raw float32 LE payload]
```

Header object:

```json
{
  "msg_type": "spectrum_frame",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "config_version": 1,
  "frame_index": 0,
  "timestamp_utc": "2026-01-01T00:00:01.000Z",
  "bin_count": 1024
}
```

- Header right-padded with ASCII spaces so the payload starts at a 4-byte offset (mirrors viewer v0.4 — lets the server forward the buffer without re-aligning).
- Payload is `bin_count × 4` bytes, float32 LE, `bin_order=low_to_high`.
- `header_len` includes padding spaces (still valid JSON trailing whitespace).

This is **byte-identical** to the viewer v0.4 frame minus the agent-side `node_id` field. That symmetry is the point: the server can validate header + payload-length and forward with a header rewrite (no payload copy).

---

## Negotiation

`connect.requested_encoding` already supports `"binary_ws"` (enum exists in `agent.domain.WireEncoding`). Server `connect_ack.wire_encoding` echoes the chosen encoding and is the source of truth.

- Agent sends `requested_encoding: "binary_ws"` if config says so.
- Server confirms `binary_ws` if it understands v0.4; falls back to `json_base64` otherwise.
- Codec used per-frame is selected from the *acked* encoding, not the requested one (already wired in `session/__init__.py` send loop).

We could drop the `json_base64` fallback entirely since deployments are version-paired, but keeping it costs ~10 lines and makes rollouts safer. **Keep it.**

---

## Server changes

`server/app/ws_agent.py` currently does `receive_text() → json.loads → base64.b64decode → validate length → re-emit binary to viewers`.

For binary frames:
- `receive_bytes()` instead of `receive_text()` when session is `binary_ws`.
- Parse header via `struct.unpack(">H", buf[:2])` + `json.loads(buf[2:2+header_len])`.
- Validate `len(buf) - 2 - header_len == bin_count * 4` → `INVALID_FRAME` if not.
- Forward to viewers: rewrite header (drop `node_id`, keep everything else) and emit `[u16 header_len][new_header][same payload bytes]`. Use `memoryview` to avoid copying the payload.

The session/registry layer is unchanged.

---

## Tests

Locking down the new contract:

1. **Codec roundtrip** (agent + server): encode binary frame, decode, assert metadata equality and `payload bytes` byte-equality.
2. **Codec rejects malformed binary**: short buffer (< 2 bytes), header_len overflows buffer, non-JSON header, payload length mismatch.
3. **Negotiation**: agent requests `binary_ws`, server acks `binary_ws` → frames go binary; agent requests `binary_ws`, server downgrades to `json_base64` → agent sends JSON.
4. **Session integration**: full handshake → first frame is binary → server decodes and re-emits to viewer → viewer receives same payload bytes.
5. **Bench (this doc, see below)**: numerical proof of wins.

No protocol-doc tests — bumping `protocol/agent_server_contract_v0_4.md` is out of scope until the bench numbers say "ship it."

---

## Bench

`agent/src/tests/bench/test_wire_encoding_bench.py` — opt-in pytest bench, deselected from normal `pytest` runs (run via `pytest -m bench`).

### What it measures

Per (encoding, fft_size, deflate-on/off) cell:

| Metric | Definition |
|---|---|
| `wire_bytes` | `len()` of the bytes that would hit the WS |
| `encode_us_p50` / `_p99` | Median / 99th-pct CPU time to encode one frame from a `SpectrumFrame` |
| `decode_us_p50` / `_p99` | Median / 99th-pct CPU time to decode one frame on the server side (b64-decode + json.loads, OR header parse + slice) |
| `loopback_ms_p50` / `_p99` | End-to-end latency through a localhost WS pair: produce frame → ws.send → ws.recv → decode |
| `deflate_ratio` | If deflate=on: `wire_bytes_compressed / wire_bytes_raw` |

### Variants

- `json_base64` — current baseline
- `json_base64 + deflate` — `permessage-deflate` enabled on `websockets.connect`
- `binary_ws` — proposed
- `binary_ws + deflate`

### fft_sizes

`1024, 4096, 16384, 131072` — covers small (RTL-SDR typical) up to spec-stress (`fft_size=131072` from contract example).

### Output

Prints a table to stdout. The "Run bench and record measurements" task takes that table and pastes it into the **Measured baseline** section below.

---

## Measured baseline

Run on Windows 11, Python 3.14, `websockets` 16.0, single-machine localhost loopback. Random-uniform dBFS noise payload (worst case for compression — real spectra compress strictly better). 500 iters for encode/decode, 200 iters for loopback.

### Encode / decode / wire size

```
 fft_size       encoding  wire_bytes  enc µs p50  enc µs p99  dec µs p50  dec µs p99  deflate
     1024    json_base64        5695        21.0        27.2        15.0        43.1    0.735
     1024      binary_ws        4323         3.0         3.9         2.7         3.2    0.857
     4096    json_base64       22079        78.5       198.1        39.3        70.0    0.726
     4096      binary_ws       16611         3.3         6.7         3.2         5.2    0.846
    16384    json_base64       87615       305.3       519.0       149.2       241.6    0.721
    16384      binary_ws       65764         4.2         5.6         4.1         6.2    0.843
   131072    json_base64      699283      2999.2      4189.7      1620.3      2478.3    0.719
   131072      binary_ws      524517        13.4        22.2        12.1        20.3    0.843
```

`deflate` column = per-message zlib ratio (compressed / raw); lower = better compression.

### Loopback (localhost WS round-trip: encode → send → ack → decode)

```
 fft_size       encoding  deflate     ms p50     ms p99
     1024    json_base64    False       0.14       0.24
     1024    json_base64     True       0.30       0.82
     1024      binary_ws    False       0.13       0.30
     1024      binary_ws     True       0.29       0.75
     4096    json_base64    False       0.23       0.42
     4096    json_base64     True       0.86       1.75
     4096      binary_ws    False       0.13       0.24
     4096      binary_ws     True       0.83       1.64
    16384    json_base64    False       0.63       1.10
    16384    json_base64     True       2.96       6.71
    16384      binary_ws    False       0.17       0.25
    16384      binary_ws     True       2.84       4.46
   131072    json_base64    False       7.82      45.45
   131072    json_base64     True      33.61      95.38
   131072      binary_ws    False       0.52       2.00
   131072      binary_ws     True      20.79      40.39
```

### Headline numbers

| Comparison (fft_size=131072) | Win |
|---|---|
| binary_ws vs json_base64, encode CPU p50 | **224× faster** (3.0 ms → 13 µs) |
| binary_ws vs json_base64, decode CPU p50 | **135× faster** (1.6 ms → 12 µs) |
| binary_ws vs json_base64, wire bytes | **25% smaller** (699 KB → 524 KB) |
| binary_ws vs json_base64, loopback p50 | **15× faster** (7.8 ms → 0.5 ms) |
| binary_ws vs json_base64, loopback p99 | **23× faster** (45 ms → 2.0 ms) |
| permessage-deflate added to binary_ws (loopback p50) | **40× slower** (0.5 ms → 21 ms) |

### Recommendation

**Adopt `binary_ws`. Skip `permessage-deflate`.**

- `binary_ws` is a strict win across every axis: smaller wire, faster encode, faster decode, lower latency, no contract cost beyond a v0.4 bump (which we already paid on the viewer side).
- The encode-CPU win is the headliner. At fft_size=131072 the agent currently burns ~3 ms of CPU per frame just on base64+JSON serialization — that's 3 ms it can't spend on FFT or driving the radio. Dropping that to 13 µs is the difference between a Pi-class device keeping up and not.
- `permessage-deflate` is a loss in this configuration. The only column where it helps is wire bytes (16-28% reduction), and we already established there's no bandwidth pressure today. The CPU cost (40× loopback latency at fft=131072) makes it a net negative until a deployment proves a bandwidth-constrained link. Document the option, leave it off.

### Caveats

- Loopback measures localhost — real WAN latency adds a fixed RTT independent of encoding. The encode/decode CPU wins (which dominate the comparison) hold regardless of network.
- Float32 dBFS payload compresses poorly (entropy is high). If we ever switch to integer or quantized payloads, the deflate calculation would change — re-run the bench then.
- Deflate ratio is measured per-message (zlib compress with no context). `permessage-deflate` with context-takeover (the WS default) compresses better across consecutive frames; the actual loopback numbers above include that, and it's still a net loss on CPU.

---

## Migration

### Phase 1 — bench (done)

Numbers above. Decision: adopt `binary_ws`, skip `permessage-deflate`.

### Phase 2 — server + agent (done, 2026-05-01)

| Change | File | Notes |
|---|---|---|
| Server accepts both encodings | `server/protocol/codec.py` | `SUPPORTED_ENCODINGS = ("json_base64", "binary_ws")` |
| Server binary decoder | `server/protocol/codec.py` | `decode_spectrum_frame_binary(buf) → SpectrumFrameMsg` |
| `SpectrumFrameMsg.payload` typed as `bytes` | `server/protocol/codec.py` | JSON path now base64-decodes inside `decode_message`; the `ws_agent` no longer does its own b64 decode |
| `LiveAgentSession.wire_encoding` field | `server/sessions/models.py` | Persisted from connect, read by frame loop |
| `connect_ack` echoes requested encoding | `server/app/ws_agent.py` | Was hardcoded `json_base64` |
| Frame loop branches on encoding | `server/app/ws_agent.py` | Uses `websocket.receive()` in binary mode to handle both binary frames and text control-plane messages |
| Agent default flips | `agent/config/__init__.py`, `agent/config/loader.py` | `WireEncoding.BINARY_WS` |
| Codec tests | `server/tests/unit/test_codec.py` | +8 tests for `decode_spectrum_frame_binary` (roundtrip + every malformed-buffer path) |
| WS agent integration tests | `server/tests/unit/test_ws_agent.py` | +7 tests: connect_ack echo, session field, full agent→server→viewer fanout, payload-length mismatch, malformed header, text-frame-in-binary-mode rejected, heartbeat stays text |
| Agent test pinning | `agent/src/tests/unit/session/test_session_manager.py` | Two `make_session*` helpers pin `wire_encoding=JSON_BASE64` so the JSON-path state-machine tests stay JSON; binary-specific helpers already existed |

**Suites green**: agent 417 pass / 1 deselected (bench), server 191 pass, server mypy clean. Agent mypy has 2 pre-existing errors in `utils/power.py` (unrelated, untouched).

### Phase 3 — docs (done, 2026-05-01)

1. `protocol/agent_server_contract_v0_4.md` written. v0.3 stays as historical reference.
2. `docs/agent_mvp_status.md` and `docs/server_mvp_status.md` updated; server status doc has a new "Phase 10 — Agent wire v0.4" section.

### Optional follow-ups

- Drop `json_base64` from `SUPPORTED_ENCODINGS` once no in-flight v0.3 agents remain. For now both stay.
- If a deployment turns up bandwidth pressure on a real WAN link, re-evaluate `permessage-deflate` (currently shelved — see "Headline numbers" above).
