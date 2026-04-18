# Server — MVP Status

**Date:** 2026-04-18
**Protocol version:** 0.3 (frozen)
**Plan reference:** `docs/server_mvp_sqlite_plan.md`

This document tracks which implementation phases are done, in-progress, or pending.

---

## Phase summary

| # | Phase | Status |
|---|-------|--------|
| 1 | Storage bootstrap | **Done** |
| 2 | Browser auth | **Done** |
| 3 | Agent + token CRUD API | **Done** |
| 4 | Runtime session registry | **Done** |
| 5 | Agent WebSocket + handshake | **Done** |
| 6 | Spectrum frame ingestion | Pending |
| 7 | Viewer WebSocket + fanout | Pending |
| 8 | Contract freeze | Pending |
| 9 | Operational polish | Pending |

---

## Phase 1 — Storage bootstrap ✓

**Goal:** Server process boots with SQLite and basic entities.

### What exists

| File | Purpose |
|------|---------|
| `server/pyproject.toml` | Dependencies: FastAPI, uvicorn, SQLAlchemy, aiosqlite, passlib, python-jose, pydantic |
| `storage/db.py` | Async engine, `SessionLocal`, `init_db()` |
| `storage/models.py` | ORM models: `User`, `Agent`, `AgentToken` |
| `storage/repositories/users.py` | `create_user`, `get_user_by_id`, `get_user_by_email` |
| `storage/repositories/agents.py` | `create_agent`, `get_agent_by_id`, `get_agents_for_user`, `get_agent_by_node_id` |
| `storage/repositories/agent_tokens.py` | `create_token`, `get_tokens_for_agent`, `get_active_token_by_hash`, `revoke_token`, `touch_last_used` |
| `app/api.py` | FastAPI factory with lifespan hook calling `init_db()` |
| `tests/unit/test_storage_repositories.py` | 9 unit tests: CRUD, ownership isolation, token revocation |

### Key constraints upheld
- All agent reads are ownership-scoped by `user_id`
- Token hashes stored, raw tokens never persisted
- `init_db()` uses SQLAlchemy async `create_all` — no migration tool yet

---

## Phase 2 — Browser auth ✓

**Goal:** User can log in and access only their own resources.

### What exists

| File | Purpose |
|------|---------|
| `auth/passwords.py` | `hash_password`, `verify_password` using `bcrypt` directly |
| `auth/browser_auth.py` | `make_session_cookie`, `read_session_cookie` via `itsdangerous` signed cookie |
| `app/http_routes.py` | `POST /auth/login`, `POST /auth/logout`, `GET /me`; router included in `api.py` |
| `app/deps.py` | `get_db` (existing) + `get_current_user` FastAPI dependency |
| `tests/unit/test_auth.py` | 7 tests: login success/failure, unknown email, `/me` unauthenticated, `/me` authenticated, logout clears cookie, tampered cookie rejected |

### Key constraints upheld
- Passwords never stored plain — only bcrypt hashes
- Cookie is HMAC-signed with `itsdangerous`; tampered values rejected with 401
- `_SECRET` is a dev constant — Phase 9 will wire it to a settings object

---

## Phase 3 — Agent + token CRUD API ✓

**Goal:** User can create agents and mint tokens via HTTP.

### What exists

| File | Purpose |
|------|---------|
| `app/agent_routes.py` | `GET /agents`, `POST /agents`, `GET /agents/{id}`, `GET /agents/{id}/tokens`, `POST /agents/{id}/tokens`, `POST /agents/{id}/tokens/{token_id}/revoke` |
| `tests/unit/test_agent_routes.py` | 11 tests: CRUD, ownership isolation, token creation/revocation/listing |

### Key constraints upheld
- All routes require `get_current_user` — unauthenticated requests get 401
- Agent reads are ownership-scoped: another user's agent returns 404 (not 403, to avoid enumeration)
- Raw token returned once at creation; only SHA-256 hash stored in DB
- Revoked tokens excluded from `GET /tokens`; double-revoke returns 404

---

## Phase 4 — Runtime session registry ✓

**Goal:** In-memory live session state exists independently of SQLite.

### What exists

| File | Purpose |
|------|---------|
| `sessions/models.py` | `LiveAgentSession` (session_id, agent_id, user_id, stream_id, config_version, heartbeat, status, frame_queue), `ViewerSubscription` (subscription_id, user_id, agent_id, session_id, send_queue) |
| `sessions/registry.py` | `SessionRegistry`: add/remove/get by session_id or agent_id, heartbeat/status/config_version update, viewer add/remove/lookup by session |
| `tests/unit/test_session_registry.py` | 30 tests: lifecycle, mutations, isolation, model defaults |

### Key constraints upheld
- No DB involvement — all state is pure in-memory
- Each `LiveAgentSession` carries its own `asyncio.Queue` for frame fanout
- Each `ViewerSubscription` carries its own `asyncio.Queue` for outbound delivery
- Registry instances are fully independent (no module-level singletons)

---

## Phase 5 — Agent WebSocket + handshake ✓

**Goal:** Authenticated agent connects and completes protocol handshake.

### What exists

| File | Purpose |
|------|---------|
| `protocol/codec.py` | `decode_message`, encode helpers (`encode_connect_ack`, `encode_stream_config_ack`, `encode_error`, `encode_disconnect`), `ProtocolError` |
| `app/ws_agent.py` | `/ws/agent` WebSocket endpoint — Bearer auth (SHA-256 hash lookup), `X-Session-Id` header, handshake order enforcement, frame loop |
| `app/api.py` | `SessionRegistry` created in lifespan, wired into `app.state.registry`; `ws_router` included |
| `storage/repositories/agents.py` | Added `get_agent_by_id_unscoped` for token-based auth (no user_id required) |
| `tests/unit/test_ws_agent.py` | 16 tests: auth failures (401), handshake violations (PROTOCOL_MISMATCH, UNSUPPORTED_ENCODING, ordering), full handshake, registry registration/deregistration, heartbeat, re-config, agent_status, spectrum_frame |

### Key constraints upheld
- `AUTH_FAILED` is HTTP 401 (via `websocket.http.response.start`) — never a WS message
- `PROTOCOL_MISMATCH` and `UNSUPPORTED_ENCODING` are fatal WS errors
- Exactly one `LiveAgentSession` per agent (enforced by `SessionRegistry`)
- Session is removed from registry on disconnect (via `finally` block)
- Test client is a lightweight ASGI WS helper (same event loop, same in-memory SQLite — no TCP, no threads)

---

## Phase 6 — Spectrum frame ingestion

**Goal:** Server accepts valid frames and rejects invalid ones.

### Plan
- Decode `spectrum_frame`, validate payload length (`bin_count * 4`)
- Handle `heartbeat` → update `last_heartbeat_at` in session
- Handle `agent_status` → cache in session
- Emit `error` on invalid messages without killing process
- Tests: valid/invalid frames, heartbeat freshness, status caching

---

## Phase 7 — Viewer WebSocket + fanout

**Goal:** Browser subscribes to its own agent and receives live frames.

### Plan
- `app/ws_viewer.py` — `/ws/viewer` endpoint
- Authenticate browser user, accept subscribe message with `agent_id`
- Ownership check: `viewer.user_id == session.user_id`
- `relay/broadcaster.py` — push config/status/frame to viewers, dead-viewer cleanup
- `relay/subscriptions.py` — viewer attach/detach logic
- Tests: ownership block, config-first delivery, live frame relay

---

## Phase 8 — Contract freeze

**Goal:** Backend API shapes are frozen and documented for web app consumption.

### Plan
- Freeze JSON shapes: `GET /agents`, `GET /agents/{id}/status`, viewer WS subscribe + outbound events
- Document shapes in markdown
- One end-to-end test: fake agent + fake viewer, assert viewer gets config then frame

---

## Phase 9 — Operational polish

**Goal:** Local demo and iteration are sane.

### Plan
- Settings object: SQLite path, server port, etc.
- Bootstrap command for creating a test user
- Structured logging: connect/disconnect/errors
- Dev README
- Smoke tests
