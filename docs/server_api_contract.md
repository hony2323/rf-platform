# Server API Contract - v0.3 (frozen)

This document defines the frozen JSON shapes for the RF Platform server API.
The web app consumes these shapes directly. Do not change them without a protocol version bump.

---

## HTTP API

All HTTP routes require a valid session cookie (`rf_session`) except `POST /auth/signup`, `POST /auth/login`, and `POST /auth/logout`.
All agent and token reads are ownership-scoped by the authenticated user's `user_id`.

### `POST /auth/signup`

Request:
```json
{ "email": "user@example.com", "password": "secret123" }
```

Response `201`:
```json
{ "id": "uuid", "email": "user@example.com" }
```

Sets `rf_session` cookie on success. Returns `409` if the email already exists.

---

### `POST /auth/login`

Request:
```json
{ "email": "user@example.com", "password": "secret" }
```

Response `200`:
```json
{ "id": "uuid", "email": "user@example.com" }
```

Sets `rf_session` cookie on success. Returns `401` on bad credentials.

---

### `POST /auth/logout`

Response `204` (no body). Clears `rf_session` cookie.

---

### `GET /me`

Response `200`:
```json
{ "id": "uuid", "email": "user@example.com" }
```

Returns `401` if not authenticated.

---

### `DELETE /me`

Request:
```json
{ "password": "secret123" }
```

Response `204` (no body). Deletes the authenticated user, all owned agents, and all owned agent tokens. Clears `rf_session` cookie.

Returns `401` if not authenticated or the password is invalid.

---

### `GET /agents`

Response `200`:
```json
[
  { "id": "uuid", "name": "My Agent", "stable_node_id": "node_abc123" }
]
```

---

### `POST /agents`

Request:
```json
{ "name": "My Agent", "stable_node_id": "node_abc123" }
```

Response `201`:
```json
{ "id": "uuid", "name": "My Agent", "stable_node_id": "node_abc123" }
```

---

### `GET /agents/{agent_id}`

Response `200`:
```json
{ "id": "uuid", "name": "My Agent", "stable_node_id": "node_abc123" }
```

Returns `404` if agent does not exist or is not owned by the caller.

---

### `DELETE /agents/{agent_id}`

Response `204` (no body). Deletes the owned agent and all tokens attached to it.

Returns `404` if agent does not exist or is not owned by the caller.

---

### `GET /agents/{agent_id}/status`

Returns the live runtime status of an agent. Reads from the in-memory session registry - not SQLite.

Response `200` when online:
```json
{
  "agent_id": "uuid",
  "online": true,
  "session_id": "ses_<hex>",
  "last_heartbeat_at": "2026-01-01T00:00:00+00:00",
  "last_status": null
}
```

`last_status` is `null` or the last `agent_status` payload sent by the agent (object, not string).

Response `200` when offline:
```json
{
  "agent_id": "uuid",
  "online": false,
  "session_id": null,
  "last_heartbeat_at": null,
  "last_status": null
}
```

Returns `404` if agent does not exist or is not owned by the caller.

---

### `GET /agents/{agent_id}/tokens`

Response `200`:
```json
[
  { "id": "uuid", "label": "prod token", "created_at": "2026-01-01T00:00:00" }
]
```

Revoked tokens are excluded.

---

### `POST /agents/{agent_id}/tokens`

Request:
```json
{ "label": "prod token" }
```

Response `201`:
```json
{
  "id": "uuid",
  "label": "prod token",
  "created_at": "2026-01-01T00:00:00",
  "token": "<raw token - returned once only>"
}
```

---

### `POST /agents/{agent_id}/tokens/{token_id}/revoke`

Response `200`:
```json
{ "id": "uuid", "label": "prod token", "created_at": "2026-01-01T00:00:00" }
```

Returns `404` if token does not exist, is already revoked, or belongs to a different agent.

---

### `DELETE /agents/{agent_id}/tokens/{token_id}`

Response `200`:
```json
{ "id": "uuid", "label": "prod token", "created_at": "2026-01-01T00:00:00" }
```

Returns `404` if the agent does not exist, is not owned by the caller, or the token does not exist.

---

## Viewer WebSocket - `/ws/viewer`

### Authentication

Cookie auth only. The `rf_session` cookie must be present on the HTTP Upgrade request.
Unauthenticated or invalid-cookie connections are rejected with HTTP 401 before the WebSocket handshake is accepted.

### Subscribe (client -> server)

First message after connect. Sent by the browser.

```json
{
  "msg_type": "subscribe",
  "agent_id": "uuid"
}
```

The `agent_id` must be owned by the authenticated user. The agent must have an active session.

### subscribe_ack (server -> client)

Sent immediately after a valid subscribe.

```json
{
  "msg_type": "subscribe_ack",
  "agent_id": "uuid",
  "session_id": "ses_<hex>",
  "stream_id": "default",
  "status": "ok"
}
```

### stream_config (server -> client)

Sent immediately after `subscribe_ack` if the agent has already sent its stream config (always true after handshake).
Also sent whenever the agent sends a new `stream_config` (reconfig).

```json
{
  "msg_type": "stream_config",
  "agent_id": "uuid",
  "session_id": "ses_<hex>",
  "stream_id": "default",
  "config_version": 1,
  "rf": {
    "center_freq_hz": 433920000,
    "sample_rate_hz": 2400000,
    "fft_size": 1024,
    "baseband_start_hz": -1200000,
    "baseband_end_hz": 1200000,
    "bin_size_hz": 2343.75,
    "bin_count": 1024,
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

`config_version` is monotonically increasing per connection. Resets to 1 when the agent reconnects (new `session_id`).

### spectrum_frame (server -> client)

Sent for every valid frame received from the agent. Payload is base64-encoded float32 LE, `bin_count x 4` bytes.

```json
{
  "msg_type": "spectrum_frame",
  "agent_id": "uuid",
  "session_id": "ses_<hex>",
  "stream_id": "default",
  "config_version": 1,
  "frame_index": 0,
  "timestamp_utc": "2026-01-01T00:00:01.000Z",
  "data": {
    "payload": "<base64 float32 LE>"
  }
}
```

`frame_index` resets to 0 on each new `config_version`. Gaps indicate dropped frames.

### error (server -> client)

Sent on subscribe failure or when the watched agent session ends. Always followed by WebSocket close.

```json
{
  "msg_type": "error",
  "code": "AGENT_OFFLINE",
  "message": "agent session ended"
}
```

Error codes:

| Code | Meaning |
|------|---------|
| `FORBIDDEN` | Agent not found or not owned by this user |
| `AGENT_OFFLINE` | Agent has no active session (at subscribe time, or session ended) |
| `INVALID_FRAME` | Malformed subscribe message |
| `INTERNAL_ERROR` | Unexpected server fault |
