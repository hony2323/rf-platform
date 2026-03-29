# RF SaaS — Protocol v0.3 (MVP, frozen)

Architecture: agent-driven live stream. No job/request-response model.

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

## Control plane — agent → server

### connect
```json
{
  "msg_type": "connect",
  "protocol_version": "0.3",
  "node_id": "node_a1b2c3",
  "agent_version": "0.3.0",
  "requested_encoding": "json_base64",
  "hardware": { "vendor": "RTL-SDR", "model": "RTL2832U", "serial": "00000001" }
}
```
`hardware.*` all optional, informational only. No `api_key` — auth is at transport.

### stream_config
```json
{
  "msg_type": "stream_config",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "timestamp_utc": "2026-03-26T10:00:00.000Z",
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

RF field authority:
- **Authoritative**: `center_freq_hz`, `sample_rate_hz`, `fft_size`
- **Derived/advisory** (receiver should verify): `baseband_start_hz = -(sample_rate_hz/2)`, `baseband_end_hz = +(sample_rate_hz/2)`, `bin_size_hz = sample_rate_hz/fft_size`
- **Payload-authoritative** (use for buffer alloc only): `bin_count` — may differ from `fft_size`

`bin_order` operational definition:
- `low_to_high`: `payload[0]` = `center_freq_hz + baseband_start_hz` (lowest freq). Index increases with frequency. Standard fftshift result.
- `natural`: `payload[0]` = DC bin. Raw FFT output order.

Send again whenever any RF or FFT parameter changes. Do not include `config_version` — server assigns it.

### heartbeat
```json
{
  "msg_type": "heartbeat",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "timestamp_utc": "2026-03-26T10:00:05.000Z"
}
```

### agent_status
```json
{
  "msg_type": "agent_status",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "timestamp_utc": "2026-03-26T10:00:05.000Z",
  "cpu_usage_pct": 34,
  "throttled": false,
  "tx_bytes_per_sec": 820000,
  "queue_depth": 3,
  "queue_fill_pct": 12,
  "drops": {
    "local_throttle": 0,
    "queue_overflow": 0,
    "server_rejected": 0
  }
}
```

Drop counter semantics (all counts since last `agent_status`):
- `local_throttle`: agent discarded — CPU/queue limit exceeded
- `queue_overflow`: agent discarded — send queue full
- `server_rejected`: server returned `INVALID_FRAME` or `FRAME_TOO_LARGE`
- Transport-layer drops are not observable here by design

---

## Control plane — server → agent

### connect_ack
```json
{
  "msg_type": "connect_ack",
  "session_id": "ses_01HX...",
  "status": "ok",
  "wire_encoding": "json_base64"
}
```

### stream_config_ack
```json
{
  "msg_type": "stream_config_ack",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "config_version": 1,
  "status": "ok"
}
```

### disconnect
```json
{
  "msg_type": "disconnect",
  "session_id": "ses_01HX...",
  "reason": "auth_expired"
}
```
Reasons: `auth_expired` | `rate_exceeded` | `server_shutdown` | `protocol_violation`

---

## Data plane — spectrum_frame

```json
{
  "msg_type": "spectrum_frame",
  "node_id": "node_a1b2c3",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "config_version": 1,
  "frame_index": 1024,
  "timestamp_utc": "2026-03-26T10:00:01.024Z",
  "data": {
    "payload": "<encoded binary>"
  }
}
```

- `timestamp_utc`: capture start, not server receipt time
- `payload`: encoding per session `wire_encoding`; semantics per `fft_semantics` for this `config_version`
- MVP: base64 float32 LE, length = `bin_count × 4` bytes
- `frame_index` resets to 0 on each new `config_version`

---

## Wire encoding (session-scoped)

| Value | Description |
|---|---|
| `json_base64` | All messages JSON text frames. `data.payload` base64-encoded. MVP default. |
| `binary_ws` | Control: JSON text. Frames: binary WS `[uint16 header_len][JSON header][raw bytes]`. Post-mvp. |
| `msgpack` | All messages msgpack binary. Post-mvp. |

Negotiated in `connect` / `connect_ack`. Immutable for session lifetime.  
Tests must assert on decoded values, never on encoded bytes.

---

## FFT semantics defaults (MVP)

| Field | MVP default | Post-mvp options |
|---|---|---|
| `kind` | `power` | `magnitude` |
| `scale` | `log` | `linear` |
| `unit` | `dBFS` | `dBm`, `none` |
| `numeric_type` | `float32` | `float64`, `uint8` |
| `bin_order` | `low_to_high` | `natural` |

---

## Errors

```json
{
  "msg_type": "error",
  "session_id": "ses_01HX...",
  "stream_id": "default",
  "config_version": 1,
  "frame_index": 1024,
  "code": "INVALID_FRAME",
  "message": "payload length does not match bin_count",
  "fatal": false
}
```

`stream_id`, `config_version`, `frame_index` omitted when not applicable.  
`fatal: true` → server closes connection after this message. Agent must reconnect.  
`fatal: false` → server continues. Agent should fix and retry.

| Code | Fatal | When |
|---|---|---|
| `AUTH_FAILED` | — | HTTP 401 on Upgrade. Never a WS message. |
| `PROTOCOL_MISMATCH` | true | Unsupported `protocol_version`. |
| `UNSUPPORTED_ENCODING` | true | `wire_encoding` not supported by server. |
| `NO_STREAM_CONFIG` | false | Frame received before `stream_config` for this (session, stream_id). |
| `INVALID_FRAME` | false | Schema or payload length validation failed. |
| `FRAME_TOO_LARGE` | false | Payload exceeds server limit. |
| `RATE_EXCEEDED` | false | Agent publishing faster than server can relay. |
| `CONFIG_REJECTED` | false | `stream_config_ack` with rejection. |
| `SESSION_EXPIRED` | true | `session_id` no longer valid. |
| `INTERNAL_ERROR` | true | Server fault. Never leaks stack trace. |

---

## Module boundaries

| Module | Owns | Contract |
|---|---|---|
| Transport | WebSocket lifecycle, HTTP Upgrade, Bearer auth, session_id issuance | Knows nothing about RF/FFT |
| Session registry | (session_id, stream_id, config_version) → stream_config cache | Enforces handshake order, owns NO_STREAM_CONFIG |
| FFT pipeline | IQ parsing, windowing, transform, payload encoding | Input: stream_config rf+semantics → Output: payload of exactly bin_count×4 bytes |
| Rate limiter | CPU/queue thresholds, frame suppression | Reads cpu_usage_pct, queue_fill_pct, tx_bytes_per_sec. Increments drops.local_throttle. |
| Agent | node_id, stream_config construction, frame_index, drop counters | Knows nothing about server routing |

---

## Post-MVP additions (do not implement now)

- `epoch_ms` (int64) replacing `timestamp_utc` strings
- `data.phase_rad`, `data.psd_db` (requires new `config_version`)
- Per-field encoding config (`uint8`, `float64`)
- `binary_ws` and `msgpack` wire encodings
- `stream_id` beyond `"default"` (multi-tuner)
- REST snapshot convenience wrapper (`GET /snapshot?node_id=...`)
