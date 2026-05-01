# RF SaaS — Protocol v0.4

Architecture: agent-driven live stream. No job/request-response model.

**Diff from v0.3** (only): `spectrum_frame` over `binary_ws` is now the default
wire encoding for the data plane. The frame layout matches the existing viewer
v0.4 binary frame. All other v0.3 messages, fields, and semantics are unchanged
and continue to apply. `json_base64` remains supported for backward compatibility.

---

## Transport

- WebSocket only (MVP)
- Auth: `Authorization: Bearer <token>` on HTTP Upgrade
- Server issues `session_id` in HTTP 101 response header `X-Session-Id`
- Wire encoding negotiated at connect, session-scoped, immutable

---

## Handshake order (enforced)

```
Agent                          Server
  |                              |
  |-- HTTP Upgrade + Bearer ---->|
  |<-- 101 + X-Session-Id -------|
  |-- connect ------------------>|
  |<-- connect_ack --------------|  (confirms session_id + wire_encoding)
  |-- stream_config ------------>|
  |<-- stream_config_ack --------|  (assigns config_version)
  |-- spectrum_frame ----------->|
  |-- spectrum_frame ----------->|
  |-- agent_status ------------->|
```

Frames before `stream_config` → `NO_STREAM_CONFIG` error.

---

## Identity

| Field | Meaning |
|---|---|
| `node_id` | Stable agent install identity. Not hardware serial. Survives hardware swap. |
| `session_id` | Server-issued per connection. Reset on reconnect. |
| `stream_id` | Sub-channel per session. Default `"default"`. Always present. |
| `config_version` | Server-assigned monotonic int per (session, stream_id). In `stream_config_ack` and every frame. |
| `frame_index` | Monotonic per (session, stream_id, config_version). Resets on config change. Gaps = dropped frames. |

---

## Wire encoding (session-scoped, immutable)

| Value | Status | Description |
|---|---|---|
| `binary_ws` | **default in v0.4** | Control plane: JSON text. `spectrum_frame`: binary WS message `[uint16_be header_len][header_json (padded)][raw float32 LE payload]`. |
| `json_base64` | supported (legacy) | All messages JSON text. `data.payload` base64-encoded float32 LE. v0.3 default. |
| `msgpack` | reserved | Not implemented. |

Negotiated in `connect.requested_encoding`; confirmed in `connect_ack.wire_encoding`. If the server does not support the requested encoding it returns a fatal `UNSUPPORTED_ENCODING` and closes the connection.

Tests must assert on decoded values, never on encoded bytes or JSON key order.

---

## Control plane — agent → server

All control-plane messages are JSON text frames in **both** wire encodings. The wire encoding only affects `spectrum_frame`.

### connect
```json
{
  "msg_type": "connect",
  "protocol_version": "0.3",
  "node_id": "node_a1b2c3",
  "agent_version": "0.4.0",
  "requested_encoding": "binary_ws",
  "hardware": { "vendor": "RTL-SDR", "model": "RTL2832U", "serial": "00000001" }
}
```
`protocol_version` stays at `"0.3"` for now — v0.4 is a wire-encoding extension, not a wire-protocol break. `hardware.*` all optional, informational only. No `api_key` — auth is at transport.

### stream_config
```json
{
  "msg_type": "stream_config",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "timestamp_utc": "2026-05-01T10:00:00.000Z",
  "rf": {
    "center_freq_hz": 433920000,
    "sample_rate_hz": 2400000,
    "fft_size": 131072,
    "baseband_start_hz": -1200000,
    "baseband_end_hz": 1200000,
    "bin_size_hz": 18.31,
    "bin_count": 100000,
    "window_fn": "hann"
  },
  "fft_semantics": {
    "kind": "power",
    "scale": "log",
    "unit": "dBFS",
    "numeric_type": "float32",
    "bin_order": "low_to_high"
  }
}
```

RF field authority and `bin_order` semantics: unchanged from v0.3. Send again whenever any RF or FFT parameter changes. Do not include `config_version` — server assigns it.

### heartbeat
```json
{
  "msg_type": "heartbeat",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "timestamp_utc": "2026-05-01T10:00:05.000Z"
}
```

### agent_status
Unchanged from v0.3. Drop counter semantics unchanged.

---

## Control plane — server → agent

### connect_ack
```json
{
  "msg_type": "connect_ack",
  "session_id": "ses_01HX...",
  "status": "ok",
  "wire_encoding": "binary_ws"
}
```
`wire_encoding` is the **server's confirmation** of which encoding will be used for the rest of the session. If the agent's `requested_encoding` is supported, it is echoed; otherwise the server emits `UNSUPPORTED_ENCODING` (fatal) and closes.

### stream_config_ack, disconnect, error
Unchanged from v0.3.

---

## Data plane — `spectrum_frame`

### Wire encoding `binary_ws` (v0.4 default)

Sent as a **binary** WebSocket message. Layout:

```
[uint16 big-endian header_len][header_json_utf8 (padded)][raw float32 LE payload]
```

- `header_len`: total byte length of the header bytes that follow, **including** any trailing space padding. Maximum 65535.
- `header_json_utf8`: a JSON object encoded as UTF-8, right-padded with ASCII spaces so the payload starts at a 4-byte offset (lets a browser construct a `Float32Array` view directly over the buffer with no copy). Padding bytes are valid trailing whitespace under JSON parsing.
- Payload: exactly `bin_count × 4` raw bytes, float32 little-endian, `bin_order=low_to_high`.

Header object:
```json
{
  "msg_type": "spectrum_frame",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "config_version": 1,
  "frame_index": 1024,
  "timestamp_utc": "2026-05-01T10:00:01.024Z",
  "bin_count": 100000
}
```

Notes:
- `timestamp_utc`: capture start, not server receipt time.
- `frame_index` resets to 0 on each new `config_version`. Gaps indicate dropped frames.
- The header carries `bin_count` so the receiver can validate `len(payload) == bin_count × 4` without consulting the cached `stream_config` — the cached config is still authoritative for FFT semantics.
- A JSON text `spectrum_frame` arriving on a `binary_ws` session is `INVALID_FRAME` (non-fatal).

### Wire encoding `json_base64` (legacy)

```json
{
  "msg_type": "spectrum_frame",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "config_version": 1,
  "frame_index": 1024,
  "timestamp_utc": "2026-05-01T10:00:01.024Z",
  "data": {
    "payload": "<base64 float32 LE, length = bin_count × 4 bytes>"
  }
}
```
Identical to v0.3.

---

## FFT semantics defaults

Unchanged from v0.3. MVP defaults: `kind=power`, `scale=log`, `unit=dBFS`, `numeric_type=float32`, `bin_order=low_to_high`.

---

## Errors

Same shape and semantics as v0.3 (`code`, `message`, `fatal`, optional `stream_id` / `config_version` / `frame_index`).

| Code | Fatal | When |
|---|---|---|
| `AUTH_FAILED` | — | HTTP 401 on Upgrade. Never a WS message. |
| `PROTOCOL_MISMATCH` | true | Unsupported `protocol_version`. |
| `UNSUPPORTED_ENCODING` | true | `requested_encoding` not in server's supported set. |
| `NO_STREAM_CONFIG` | false | Frame received before `stream_config` for this (session, stream_id). |
| `INVALID_FRAME` | false | Schema or payload-length validation failed. Includes: text `spectrum_frame` on a `binary_ws` session; binary `spectrum_frame` whose declared `header_len` overflows the buffer or whose header is not valid JSON; payload length ≠ `bin_count × 4`; missing required header field. |
| `FRAME_TOO_LARGE` | false | Payload exceeds server limit. |
| `RATE_EXCEEDED` | false | Agent publishing faster than server can relay. |
| `CONFIG_REJECTED` | false | `stream_config_ack` with rejection. |
| `SESSION_EXPIRED` | true | `session_id` no longer valid. |
| `INTERNAL_ERROR` | true | Server fault. Never leaks stack trace. |

---

## Why v0.4 (the rationale, briefly)

Measured at fft_size=131072 (`docs/agent_wire_v0_4_plan.md`):

- Binary frame is **25% smaller** on the wire than `json_base64` (no base64 expansion, header overhead stays a small constant).
- Encode CPU **224× faster** at the agent (3.0 ms → 13 µs per frame).
- Decode CPU **135× faster** at the server.
- Localhost loopback **15× lower** p50, **23× lower** p99.
- Server-to-viewer hop already runs the same binary layout, so the relay path becomes a header rewrite with no payload copy.

`permessage-deflate` was measured and rejected: it adds 16–28% wire compression on noise payloads but slows localhost p50 latency 40× because of the per-message compress/decompress CPU cost. Document and leave off until a deployment exhibits real bandwidth pressure.

---

## Module boundaries

Same as v0.3 — agent transport knows nothing about RF, session registry enforces handshake order, FFT pipeline produces `bin_count × 4` payload bytes, rate limiter handles drops.

---

## Post-v0.4 additions (do not implement now)

- `epoch_ms` (int64) replacing `timestamp_utc` strings
- `data.phase_rad`, `data.psd_db` (requires new `config_version`)
- Per-field encoding config (`uint8`, `float64`)
- `msgpack` wire encoding
- `stream_id` beyond `"default"` (multi-tuner)
- REST snapshot convenience wrapper (`GET /snapshot?node_id=...`)
