# Server + Web MVP Implementation Plan (SQLite-first)

**Date:** 2026-04-18  
**Scope:** backend server + minimal web-facing API for the RF platform MVP  
**Assumptions locked:** protocol v0.3 is frozen, monorepo layout stays as agreed, live path stays in-memory, SQLite is used only for persistent control-plane data.

---

## 1. Goal

Implement the first working server-side MVP for the RF platform with these product capabilities:

- user can log in and access their own space
- user can create agent API tokens
- agent can connect with API token and send FFT data
- user can see a live spectrogram waterfall for each of their agents
- live latency target: under 2 seconds end-to-end

This plan is intentionally **SQLite-first**:

- **SQLite** stores persistent product data
- **in-memory runtime state** handles all live streaming state
- **no external DB, Redis, broker, or object store** in MVP

---

## 2. Non-goals for MVP

Do **not** implement these now:

- multi-tenant orgs / teams / RBAC
- historical spectrum storage
- replay / scrub / archive browsing
- multi-instance backend coordination
- Redis pubsub
- binary_ws / msgpack transport
- REST polling for live data
- mobile client support
- fine-grained permissions
- agent fleet automation / remote config push UI

---

## 3. Architecture decision

### Persistent state: SQLite

Use SQLite for:

- users
- agents
- agent token hashes
- basic ownership metadata
- optional lightweight audit rows
- optional last-seen / last-status snapshots

### Ephemeral runtime state: memory only

Keep these in memory only:

- connected agent sessions
- websocket objects
- viewer subscriptions
- latest stream config per live session
- latest live frame cache per session (optional but useful)
- heartbeat freshness / live connection status

### Hard rule

**Never put live spectrum frames on the SQLite hot path.**

Live path must be:

1. agent websocket frame arrives
2. validate against current session/config
3. fan out to subscribed viewers
4. optionally update tiny runtime metadata in memory

The DB is not part of the live frame loop.

---

## 4. Target monorepo placement

Add implementation under the existing monorepo shape:

```text
server/
  pyproject.toml
  src/server/
    __init__.py
    app/
    auth/
    domain/
    protocol/
    sessions/
    relay/
    storage/
    transport/
    tests/
      unit/
      integration/
      fixtures/
```

The web app stays separate in `web/`, but this plan focuses mainly on `server/`.

---

## 5. Backend module boundaries

## 5.1 `storage/`

Owns SQLite and repository logic.

Suggested files:

```text
storage/
  db.py
  models.py
  repositories/
    users.py
    agents.py
    agent_tokens.py
```

Responsibilities:

- create engine / session factory
- initialize tables
- small repo methods only
- no websocket logic here
- no runtime live state here

## 5.2 `auth/`

Owns browser auth and agent token auth.

Suggested files:

```text
auth/
  passwords.py
  browser_auth.py
  agent_tokens.py
  dependencies.py
```

Responsibilities:

- hash/verify passwords
- issue/verify browser auth session or JWT
- create raw agent tokens
- hash agent tokens before storage
- authenticate agent bearer token on websocket upgrade

### Important split

Keep these as two separate auth concerns:

1. **browser auth**: user logs into dashboard
2. **agent auth**: bearer token used only by agent websocket

Do not reuse one token type for both.

## 5.3 `sessions/`

Owns live agent sessions.

Suggested files:

```text
sessions/
  models.py
  registry.py
  service.py
  ownership.py
```

Responsibilities:

- create runtime session when agent websocket authenticates
- store `session_id`, `agent_id`, `user_id`, current `stream_id`, current `config_version`
- cache current stream config
- track last heartbeat / online status
- remove session on disconnect
- expose lookup for viewer subscription checks

## 5.4 `protocol/`

Owns server-side protocol handling for protocol v0.3.

Suggested files:

```text
protocol/
  codec.py
  validators.py
  handlers.py
```

Responsibilities:

- decode agent messages
- validate handshake order
- validate payload sizes / required fields
- produce `connect_ack`, `stream_config_ack`, `error`, `disconnect`

## 5.5 `relay/`

Owns browser fanout.

Suggested files:

```text
relay/
  broadcaster.py
  subscriptions.py
  messages.py
```

Responsibilities:

- subscribe viewer websockets to one agent/session
- fan out config/status/frame events
- remove dead viewers
- optionally cache latest frame + latest config for late joiners

## 5.6 `app/`

Owns FastAPI wiring.

Suggested files:

```text
app/
  api.py
  deps.py
  http_routes.py
  ws_agent.py
  ws_viewer.py
  startup.py
```

Responsibilities:

- expose HTTP API
- expose agent websocket endpoint
- expose viewer websocket endpoint
- wire dependencies together

---

## 6. Data model (SQLite)

Use a minimal schema.

## 6.1 `users`

Fields:

- `id` (string/uuid or integer)
- `email` (unique)
- `password_hash`
- `created_at`
- `updated_at`

## 6.2 `agents`

Fields:

- `id`
- `user_id` (fk users)
- `name`
- `stable_node_id` (unique)
- `created_at`
- `updated_at`
- `last_seen_at` (nullable)
- `last_status` (nullable JSON/text, optional)

Notes:

- `stable_node_id` is the logical agent identity
- every agent belongs to exactly one user in MVP

## 6.3 `agent_tokens`

Fields:

- `id`
- `agent_id` (fk agents)
- `label` (optional human label)
- `token_hash`
- `created_at`
- `revoked_at` (nullable)
- `last_used_at` (nullable)

Notes:

- store only hashed token values
- return raw token only once at creation time

## 6.4 Optional `audit_events` (can be skipped in first pass)

Fields:

- `id`
- `user_id`
- `agent_id` (nullable)
- `event_type`
- `created_at`
- `payload_json`

This is optional. Skip it if it slows implementation.

---

## 7. Runtime in-memory model

Create explicit runtime models separate from SQLite models.

Suggested `LiveAgentSession` fields:

- `session_id`
- `agent_id`
- `user_id`
- `node_id`
- `connected_at`
- `last_heartbeat_at`
- `current_stream_id`
- `current_config_version`
- `current_stream_config`
- `agent_ws`
- `viewer_ids` or direct viewer connections
- `latest_status`
- `latest_frame` (optional)

Suggested `ViewerSubscription` fields:

- `viewer_connection_id`
- `user_id`
- `agent_id`
- `ws`
- `subscribed_at`

Do not try to persist these.

---

## 8. API and websocket surface

## 8.1 Browser HTTP API

Implement this exact first-pass API:

### Auth

- `POST /auth/register` *(optional if you want manual bootstrap instead)*
- `POST /auth/login`
- `POST /auth/logout`
- `GET /me`

### Agents

- `GET /agents`
- `POST /agents`
- `GET /agents/{agent_id}`
- `GET /agents/{agent_id}/status`

### Agent tokens

- `POST /agents/{agent_id}/tokens`
- `GET /agents/{agent_id}/tokens`
- `POST /agents/{agent_id}/tokens/{token_id}/revoke`

### Notes

- all browser API routes must require authenticated user except login/register
- all reads must be ownership-scoped by `user_id`
- do not expose token hashes

## 8.2 Agent websocket

Endpoint:

- `GET /ws/agent`

Behavior:

1. read `Authorization: Bearer <token>` from websocket upgrade request
2. validate token against stored hash
3. resolve owning `agent_id` and `user_id`
4. create runtime session with fresh `session_id`
5. include `X-Session-Id` in upgrade response if framework path allows it
6. enforce frozen protocol order after websocket opens:
   - `connect`
   - `connect_ack`
   - `stream_config`
   - `stream_config_ack`
   - `spectrum_frame` / `heartbeat` / `agent_status`

## 8.3 Viewer websocket

Endpoint:

- `GET /ws/viewer`

Behavior:

1. authenticate browser user
2. browser sends a subscribe message with `agent_id`
3. verify the agent belongs to current user
4. attach viewer to that live session if online
5. push:
   - current stream config if available
   - latest known status if available
   - live spectrum frames

Do not implement generic arbitrary topic subscriptions.

Use a narrow MVP subscription model:

- one viewer socket can subscribe to one agent at a time
- changing agent means resubscribe or reconnect

That is enough.

---

## 9. Protocol handling rules

Use the already frozen protocol v0.3 exactly.

## 9.1 Session lifecycle

For every new authenticated agent websocket:

- create fresh `session_id`
- session starts in handshake state
- require `connect` first
- require `stream_config` before any `spectrum_frame`
- assign `config_version` on accepted config
- reject frames before config with `NO_STREAM_CONFIG`

## 9.2 Validation rules

At minimum validate:

- `protocol_version`
- required fields on all messages
- `session_id` consistency
- `stream_id` consistency
- `config_version` consistency on frames
- `payload` byte length equals `bin_count * 4` for MVP json_base64 float32 payloads
- frame ordering can be accepted with gaps, but malformed indices should be logged

## 9.3 Server responses

Support these outbound server messages first:

- `connect_ack`
- `stream_config_ack`
- `error`
- `disconnect`

## 9.4 Ownership rule

Every live session must carry `user_id`.

Viewer fanout must always check that:

- `viewer.user_id == live_session.user_id`

No exceptions.

---

## 10. Live fanout design

Keep it simple.

## 10.1 Broadcaster behavior

When a valid spectrum frame arrives:

1. locate runtime live session
2. optionally store as `latest_frame`
3. iterate subscribed viewers
4. push a viewer event containing the frame
5. remove viewers whose sockets are dead

## 10.2 Suggested viewer event types

Use simple JSON messages to browser.

### `viewer_stream_config`

Contains:

- `agent_id`
- `session_id`
- `stream_id`
- `config_version`
- RF config
- FFT semantics

### `viewer_status`

Contains:

- `agent_id`
- online/offline
- last heartbeat
- optional agent_status payload

### `viewer_spectrum_frame`

Contains:

- `agent_id`
- `session_id`
- `stream_id`
- `config_version`
- `frame_index`
- `timestamp_utc`
- `payload`

Browser can decode and draw directly.

## 10.3 Slow viewer policy

Do not block the agent on slow viewers.

Pick one of these simple policies:

- best option for MVP: if viewer send backlog grows, drop oldest pending frame for that viewer
- acceptable fallback: disconnect slow viewer

Do **not** backpressure the agent websocket based on browser slowness in MVP.

---

## 11. Auth design decisions

## 11.1 Browser auth

Use one of these:

- cookie-based session auth
- JWT bearer for browser API

Recommended for MVP: **simple signed cookie session** if you want less frontend auth hassle.

If using JWT, keep it simple and short-lived.

## 11.2 Agent token format

Use opaque random tokens.

Recommended shape:

```text
agt_<random high-entropy string>
```

Store only `sha256(token)` or better.

Token creation flow:

1. generate raw token
2. hash token
3. persist hash
4. return raw token once in API response

Verification flow:

1. take bearer token from agent websocket
2. hash it
3. compare against stored non-revoked hashes

Do not invent signed self-describing agent tokens yet.

---

## 12. Deployment stance for MVP

Single backend instance only.

That means:

- SQLite file on local disk
- in-memory sessions in one process
- viewer must hit same backend instance as agent session owner process

This is fine for MVP.

Do not pretend this is horizontally scalable. It isn’t, and that’s okay for now.

---

## 13. Implementation phases

## Phase 1 — bootstrapping and storage

Goal: get the server process booting with SQLite and basic entities.

Tasks:

1. create `server/pyproject.toml` dependencies
2. add FastAPI app skeleton
3. add SQLite engine/session setup
4. define ORM models for `users`, `agents`, `agent_tokens`
5. add startup hook that initializes tables
6. add repository layer with minimal CRUD methods
7. add unit tests for repositories

Acceptance criteria:

- app boots locally
- SQLite file is created on disk
- tables are created automatically or via a minimal init path
- can create/read users and agents in tests

## Phase 2 — browser auth

Goal: user can log in and access only their own resources.

Tasks:

1. implement password hashing helpers
2. implement login route
3. implement current-user dependency
4. implement logout route
5. implement `GET /me`
6. add auth tests

Acceptance criteria:

- login works
- protected routes reject unauthenticated users
- protected routes expose correct current user

## Phase 3 — agent and token management API

Goal: user can create agents and mint tokens.

Tasks:

1. implement `POST /agents`
2. implement `GET /agents`
3. implement `GET /agents/{id}`
4. implement `POST /agents/{id}/tokens`
5. implement token revocation route
6. hash tokens before storing
7. return raw token only on creation
8. add ownership tests

Acceptance criteria:

- user can create an agent
- user can mint a token for that agent
- user cannot access another user’s agent or tokens

## Phase 4 — runtime session registry

Goal: live runtime state exists independently of SQLite.

Tasks:

1. implement `LiveAgentSession` model
2. implement `SessionRegistry`
3. add methods:
   - create_session
   - remove_session
   - get_by_session_id
   - get_by_agent_id
   - update_heartbeat
   - set_stream_config
   - set_latest_status
4. add tests for lifecycle and cleanup

Acceptance criteria:

- registry creates and removes sessions correctly
- registry ties session to `agent_id` and `user_id`
- stale sessions are removed on disconnect

## Phase 5 — agent websocket auth and handshake

Goal: authenticated agent can connect and establish a valid live session.

Tasks:

1. add `/ws/agent`
2. read bearer token from upgrade request
3. verify token hash against SQLite
4. create runtime session
5. enforce protocol order
6. send `connect_ack`
7. accept `stream_config`
8. assign `config_version`
9. send `stream_config_ack`
10. add integration tests with fake agent client

Acceptance criteria:

- invalid token is rejected
- valid token creates a live session
- protocol violations return proper error
- config is stored in registry

## Phase 6 — spectrum frame ingestion

Goal: server accepts valid frames and rejects invalid ones.

Tasks:

1. decode `spectrum_frame`
2. validate against current session/config
3. verify payload length matches `bin_count * 4`
4. update minimal runtime metadata
5. handle `heartbeat`
6. handle `agent_status`
7. emit server `error` on invalid messages without killing whole process
8. add unit and integration tests

Acceptance criteria:

- valid frames are accepted
- invalid payload lengths are rejected
- heartbeat updates online freshness
- agent status is cached in runtime state

## Phase 7 — viewer websocket and fanout

Goal: browser can subscribe to its own agent and receive live frames.

Tasks:

1. add `/ws/viewer`
2. authenticate browser user
3. implement subscribe message with `agent_id`
4. verify ownership
5. attach viewer to runtime session
6. push config/status/frame events
7. implement dead-viewer cleanup
8. add integration tests with fake viewer socket

Acceptance criteria:

- viewer can subscribe only to owned agent
- viewer gets config first when available
- viewer receives live frames while agent is streaming

## Phase 8 — minimal web integration contract

Goal: backend is clean enough for web app to consume.

Tasks:

1. freeze JSON shapes for:
   - `GET /agents`
   - `GET /agents/{id}/status`
   - viewer websocket subscribe message
   - viewer websocket outbound events
2. document them in markdown
3. add one end-to-end test with fake agent + fake viewer

Acceptance criteria:

- web app can render dashboard and single-agent live view from documented server contract

## Phase 9 — operational polish for MVP

Goal: make local demo and coding-agent iteration sane.

Tasks:

1. add config settings object for server
2. make SQLite path configurable
3. add bootstrap command or startup path for creating a test user
4. add logging around connect/disconnect/errors
5. add dev README
6. add smoke tests

Acceptance criteria:

- fresh clone can boot locally with minimal steps
- logs are readable enough to debug session flow

---

## 14. File-by-file first pass skeleton

This is the recommended first implementation pass.

```text
server/src/server/
  app/
    api.py                # FastAPI app factory
    http_routes.py        # auth + agents + tokens
    ws_agent.py           # agent websocket endpoint
    ws_viewer.py          # viewer websocket endpoint
    deps.py               # current_user, db session, registry deps

  auth/
    passwords.py          # hash/verify password
    browser_auth.py       # login/session helpers
    agent_tokens.py       # create/verify opaque tokens

  storage/
    db.py                 # engine, SessionLocal, init_db
    models.py             # ORM models
    repositories/
      users.py
      agents.py
      agent_tokens.py

  sessions/
    models.py             # LiveAgentSession, ViewerSubscription
    registry.py           # in-memory registry

  protocol/
    codec.py              # decode/encode server-side protocol messages
    validators.py         # field / payload checks

  relay/
    broadcaster.py        # push config/status/frame to viewers
    subscriptions.py      # viewer attach/detach logic

  domain/
    schemas.py            # pydantic API schemas for HTTP + viewer messages
```

Do not create more files than this in the first pass.

---

## 15. Testing strategy

Keep tests close to module boundaries.

## 15.1 Unit tests

Implement first:

- token hash + verify
- user auth helpers
- repository CRUD + ownership queries
- session registry lifecycle
- protocol validation for handshake order
- frame payload length validation
- broadcaster add/remove viewer behavior

## 15.2 Integration tests

Implement next:

1. login + create agent + create token
2. agent websocket valid handshake
3. agent sends stream config and one frame
4. viewer subscribes to owned agent and receives frame
5. viewer denied when subscribing to foreign agent
6. token revoked → next agent connection rejected

## 15.3 End-to-end target test

One valuable end-to-end MVP test:

1. create user
2. create agent + token
3. connect fake agent via websocket
4. complete handshake
5. connect fake browser viewer as same user
6. subscribe to agent
7. send one valid frame from agent
8. assert viewer receives config then frame

If that works, the backend MVP skeleton is real.

---

## 16. Suggested implementation order for the coding agent

Strict order:

1. storage/db models
2. browser auth
3. agent/token CRUD
4. in-memory session registry
5. agent websocket auth
6. protocol handshake
7. frame ingestion validation
8. viewer websocket subscription
9. broadcaster fanout
10. end-to-end integration test

Do not start from websocket fanout before auth and ownership exist.

---

## 17. Explicit constraints for the coding agent

The coding agent must obey these constraints:

1. **Do not redesign the protocol.** Use frozen protocol v0.3 as-is.
2. **Do not add external infra.** No Postgres, Redis, Kafka, Celery, or broker.
3. **Do not persist live frames in SQLite.**
4. **Do not mix browser auth and agent auth into one token type.**
5. **Do not add org/team/multi-tenant abstractions.** User owns agents directly.
6. **Do not introduce horizontal-scaling abstractions.** Single process is enough.
7. **Do not over-generalize subscriptions.** One viewer subscribes to one agent at a time.
8. **Do not make the web app server smart.** Business logic stays in backend.
9. **Do not skip ownership checks.** Every agent/viewer relation must be scoped by `user_id`.
10. **Do not invent a historical storage feature.** Live only.

---

## 18. Definition of done for backend MVP

Backend MVP is done when all of these are true:

- user can log in
- user can create an agent
- user can mint an agent token
- agent can connect with that token
- server creates a runtime live session
- agent completes handshake and sends valid frames
- server accepts heartbeat and status
- viewer socket can subscribe to owned agent
- viewer receives stream config and live frames
- unauthorized cross-user access is blocked
- no external DB/service is required beyond local SQLite file

---

## 19. Recommended first milestone

The first milestone should be this exact vertical slice:

1. create one user
2. create one agent
3. mint one token
4. fake agent connects
5. fake agent completes handshake
6. fake agent sends one frame
7. fake viewer subscribes
8. fake viewer receives that frame

That proves the architecture is correct.

Everything else is refinement.

