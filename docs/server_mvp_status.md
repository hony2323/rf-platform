# Server — MVP Status

**Date:** 2026-04-30
**Agent protocol version:** 0.3 (frozen — `protocol/agent_server_contract_v0_3.md`)
**Viewer contract version:** 0.4 (`docs/server_api_contract.md`) — viewer `spectrum_frame` is now a binary WS message; agent contract unchanged.
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
| 6 | Spectrum frame ingestion | **Done** |
| 7 | Viewer WebSocket + fanout | **Done** |
| 8 | Contract freeze | **Done** |
| 9 | Operational polish | **Done** |

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

## Phase 6 — Spectrum frame ingestion ✓

**Goal:** Server accepts valid frames and rejects invalid ones.

### What exists

| File | Purpose |
|------|---------|
| `sessions/models.py` | Added `bin_count: int = 0` to `LiveAgentSession` |
| `app/ws_agent.py` | Extracts `bin_count` from `stream_config.rf`, validates `spectrum_frame` payload length (`bin_count × 4`), enqueues valid frames on `session.frame_queue`; re-config updates `session.bin_count` |
| `tests/unit/test_ws_agent.py` | 5 new tests: valid frame enqueued, payload too short, payload too long, invalid base64, reconfig updates bin_count |

### Key constraints upheld
- Invalid payload → nonfatal `INVALID_FRAME` with `stream_id`, `config_version`, `frame_index`; connection stays alive
- Missing `rf.bin_count` in handshake stream_config → fatal `INVALID_FRAME`, connection closed
- Valid frames put on `session.frame_queue` as `SpectrumFrameMsg` for Phase 7 fanout
- Tests assert on decoded float values, never raw base64 bytes

---

## Phase 7 — Viewer WebSocket + fanout ✓

**Goal:** Browser subscribes to its own agent and receives live frames.

### What exists

| File | Purpose |
|------|---------|
| `sessions/models.py` | Added `last_stream_config: dict \| None`, `last_config_version: int \| None` to `LiveAgentSession`; bounded `ViewerSubscription.send_queue` to 64 |
| `sessions/registry.py` | `update_stream_config` extended to also cache `last_stream_config` / `last_config_version` |
| `protocol/codec.py` | Viewer outbound encoders: `encode_viewer_subscribe_ack`, `encode_viewer_stream_config`, `encode_viewer_spectrum_frame`, `encode_viewer_error` |
| `app/ws_agent.py` | Stores config cache on handshake and reconfig; fan-out to viewer `send_queue`s after each valid frame (put_nowait, drop on QueueFull) |
| `app/ws_viewer.py` | `/ws/viewer` — cookie auth (pre-accept), subscribe → ownership check → AGENT_OFFLINE if offline → subscribe_ack + stream_config delivery → drain loop |
| `app/api.py` | Viewer router included |
| `tests/unit/test_ws_viewer.py` | 7 tests: online subscribe, ack+config delivery, single viewer frame, two-viewer fanout, unowned FORBIDDEN, offline AGENT_OFFLINE, slow viewer drop |

### Key constraints upheld
- Auth is cookie-based (browser user); unauthed requests get HTTP 401 before WS accept
- Ownership verified via `get_agent_by_id(db, agent_id, user_id)`; no subscription without ownership
- `AGENT_OFFLINE` returned if agent has no active session; connection closed immediately
- Config-first: `stream_config` sent to viewer immediately after `subscribe_ack`
- Fan-out is synchronous in agent ingestion path; full viewer queues drop frames silently (never block agent)

---

## Phase 8 — Contract freeze ✓

**Goal:** Backend API shapes are frozen and documented for web app consumption.

### What exists

| File | Purpose |
|------|---------|
| `app/agent_routes.py` | Added `GET /agents/{id}/status` — returns live session state from registry (online/offline, session_id, last_heartbeat_at, last_status) |
| `docs/server_api_contract.md` | Frozen JSON shapes: all HTTP endpoints + viewer WS subscribe/ack/stream_config/spectrum_frame/error |
| `tests/unit/test_e2e.py` | End-to-end vertical slice: agent handshake → viewer subscribe → frame delivery → agent disconnect → status offline |

### Key constraints upheld
- Status endpoint reads from in-memory registry only; DB is not hit for live state
- `last_status` is deserialized from JSON string before returning (not raw string)
- Contract doc covers all viewer WS error codes and their semantics

---

## Phase 9 — Operational polish ✓

**Goal:** Local demo and iteration are sane.

### What exists

| File | Purpose |
|---|---|
| `config/settings.py` | `Settings` dataclass + `load_settings()` — reads `RF_DB_PATH`, `RF_HOST`, `RF_PORT`, `RF_SESSION_SECRET`, `RF_SESSION_COOKIE_NAME`, `RF_SESSION_COOKIE_SECURE` from env with safe defaults |
| `app/auth_config.py` | Updated to read `RF_SESSION_SECRET` / `RF_SESSION_COOKIE_NAME` / `RF_SESSION_COOKIE_SECURE` from env |
| `app/api.py` | `create_app()` now accepts `db_path=None`; falls back to `settings.db_path`; exposes top-level `app` for `uvicorn server.app.api:app` |
| `app/bootstrap.py` | `python -m server.app.bootstrap --email … --password …` — creates user, exits 1 if duplicate |
| `app/ws_agent.py` | `INFO` logs at connect, session start, session end; `ERROR` on unexpected exception |
| `app/ws_viewer.py` | `INFO` logs at subscribe and unsubscribe; `ERROR` on unexpected exception |
| `server/README.md` | Dev setup: install, bootstrap, run, config table, API surface, test commands, log format |
| `tests/unit/test_bootstrap.py` | 5 smoke tests: bootstrap creates user, duplicate → exit 1, password is hashed, settings defaults, settings from env |

### Key constraints upheld
- Tests pass explicit `:memory:` to `create_app()` — settings are not consulted in tests
- `SESSION_SECRET` default is labeled "dev-secret-change-in-production" in docs
- Bootstrap is idempotent-safe: duplicate email exits non-zero rather than silently overwriting
