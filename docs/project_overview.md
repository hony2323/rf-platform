# RF Platform — Full Project Description

**Date:** 2026-04-23
**Protocol version:** 0.3 (frozen)

## 1. What the project is

A monorepo for **live RF spectrum streaming**. SDR agents on edge devices read IQ samples, compute FFT frames, and push them over WebSocket to a central relay server, which fans them out to authenticated browser clients rendering a waterfall display. The agent/server wire contract is **v0.3, frozen for MVP** (`protocol/agent_server_contract_v0_3.md`).

```
SDR hardware / file / simulator
        │
        ▼  IQ bytes
   [agent]  parse_iq → FFT → JSON+base64
        │
        ▼  wss:// (Bearer token)
   [server]  auth → session registry → fanout
        │
        ▼  wss:// (cookie session)
   [web]  WaterfallCanvas (ring-buffer GPU-ish render)
```

## 2. Repository layout

```
agent/        Python 3.10+, asyncio, websockets, numpy — pip install -e ".[dev]"
server/       Python 3.10+, FastAPI, SQLAlchemy async + aiosqlite, bcrypt, itsdangerous
web/          React 18, Vite 6, TanStack Query, React Router v6, Tailwind v3, TS strict
protocol/     Docs only (v0.3 contract + IQ input schema) — frozen
docs/         Architecture + per-component status
scripts/      Dev tooling: run_demo.py, fake_server.py, SigMF/WAV size reducers
recordings/   Local-only RF captures (gitignored)
agent-configs/  Local TOML configs (gitignored)
.github/workflows/  agent-ci.yml, server-ci.yml, web-ci.yml
docker-compose.yml + Caddyfile + server/Dockerfile  (deployed to api.rf-platform.xyz)
```

`.env.production`, `Caddyfile`, `deployment_checklist`, and `agent-configs/` are gitignored but present in the working tree.

## 3. Current status per component

| Component | State |
|---|---|
| `agent/` | **Feature-complete for file/simulator sources.** All four stages (source → parse+FFT → session → WS) run as concurrent asyncio tasks under `AgentRunner`. Production CLI `rf-agent connect` exists with TOML + env + flag precedence. Reconnect with exponential backoff + jitter. |
| `server/` | **MVP complete, all 9 phases done** (see `docs/server_mvp_status.md`). HTTP + both WebSocket endpoints. SQLite via async SQLAlchemy. 117 tests. Contract frozen. Operational polish (env-driven settings, logging, bootstrap CLI) shipped. |
| `web/` | **MVP complete, all 10 phases done** (see `docs/web_mvp_status.md`). Login/signup, agents list, tokens, live waterfall, connect guide, error polish. Waterfall uses `@hony2323/waterfall-canvas` (vendored). Deployed to Vercel. |
| `protocol/` | **Frozen v0.3.** Only `json_base64` encoding. Post-MVP items explicitly deferred. |

Recent commits (master) show very active feature work: agent/token delete endpoints, MVP quota limits (5 users / 5 agents per user / 1 active token per agent), signup + account deletion, connect guide, mobile responsiveness, touch pan/pinch zoom on waterfall, Windows sleep prevention, Vercel Speed Insights.

## 4. Agent — detail

**Modules** (`agent/src/agent/`):
- `domain/` — frozen dataclasses (`IQDescriptor`, `RFConfig`, `SpectrumFrame`, `AgentMetrics`, `DropCounters`, `ConnectionState`). No I/O.
- `source/` — `IQSource` protocol; `SimulatorSource`, `WavSource`, `SigMFSource`. No real hardware source yet.
- `processing/` — stateless `parse_iq` (normalize + DC-remove), `FFTProcessor` (Hann → FFT → fftshift → log-power dBFS), `IQProcessor` (orchestrator, holds byte remainder across chunks).
- `session/` — 5-state handshake machine (DISCONNECTED→CONNECTING→CONNECTED→CONFIGURED→STREAMING), `_send_loop` with pluggable `BandwidthLimiter` (`DecimateLimiter` or `DropLimiter`).
- `transport/` — `WebSocketTransport` wraps `websockets` library, adds Bearer header, reads `X-Session-Id`.
- `telemetry/` — `MetricsCollector`, `PipelineTiming` (rolling p50/p99), `TelemetryLoop` (heartbeat + agent_status concurrent tasks).
- `protocol/` — `JsonBase64Codec` encodes all outbound/decodes all inbound wire messages.
- `config/` — typed `AgentConfig` with a validation boundary, TOML loading.
- `app/` — `AgentRunner.run_forever()` retries `run_once()` with backoff; injected `RunnerFactories` let tests replace any component.
- `cli.py` — `rf-agent connect` with precedence CLI > TOML > env > defaults; supports SigMF/WAV/simulator.

Tests: unit across every module + integration tests against `scripts/fake_server.py` (real TCP).

**Agent gaps documented** in `docs/agent_mvp_status.md` (read this before touching agent code):
- No real SDR hardware source (RTL-SDR, HackRF) — the whole point, missing.
- `SigMFSource` has no rate limiting (WAV and Simulator do).
- `HardwareInfo` exists but isn't wired into `encode_connect`.
- No graceful shutdown (SIGTERM draining + final `agent_status`).
- Frame timestamps are assigned at dequeue, not capture — fine for files, wrong for live hardware.
- `RATE_EXCEEDED` not acted on; `CONFIG_REJECTED` should raise `SessionError`, currently just logs and continues.

## 5. Server — detail

**Modules** (`server/src/server/`):
- `app/api.py` — FastAPI factory; lifespan runs `init_db()` and creates `SessionRegistry` in `app.state`; optional CORS; includes 4 routers.
- `app/http_routes.py` — `/auth/signup`, `/auth/login`, `/auth/logout`, `/me`, `DELETE /me` (password-gated, cascades to owned agents + tokens + live sessions).
- `app/agent_routes.py` — `GET/POST/DELETE /agents`, `/agents/{id}/status` (reads in-memory registry, not DB), tokens CRUD + revoke. Enforces MVP caps: 5 agents/user, 1 active token/agent.
- `app/ws_agent.py` — Bearer auth pre-accept (401 via raw ASGI send), handshake order enforcement, frame validation (`bin_count*4` byte payload), fanout to viewer queues with `put_nowait` + drop-on-full.
- `app/ws_viewer.py` — Cookie auth pre-accept; subscribe → ownership check → AGENT_OFFLINE if offline; races recv/send/close-signal in drain loop.
- `app/bootstrap.py` — `python -m server.app.bootstrap --email ... --password ...` CLI to create first user.
- `auth/passwords.py` — bcrypt direct (passlib crashes on Python 3.14 per project note).
- `auth/browser_auth.py` — itsdangerous signed session cookies.
- `config/settings.py` — env-driven: `RF_DB_PATH`, `RF_HOST`, `RF_PORT`, `RF_SESSION_SECRET`, `RF_SESSION_COOKIE_NAME`, `RF_SESSION_COOKIE_SECURE`, `RF_CORS_ORIGINS`.
- `sessions/registry.py` — in-memory `SessionRegistry` with secondary `agent_id → session_id` index; evicts viewers when session removed.
- `sessions/models.py` — `LiveAgentSession` (with frame_queue, last_stream_config cache), `ViewerSubscription` (bounded send_queue=64, `closed: asyncio.Event`).
- `storage/` — SQLAlchemy async engine, `User`/`Agent`/`AgentToken` ORM with cascade deletes, repositories per entity. Tokens stored as SHA-256 hashes.
- `protocol/codec.py` — inbound decoder + outbound encoders, `ProtocolError`.

Tests (117): storage repos, auth, HTTP routes, agent WS handshake/errors, viewer WS subscribe/fanout/slow-viewer-drop, full end-to-end vertical slice, CORS, bootstrap.

**Server gaps / observations:**
- `storage/models.py` has `last_seen_at`/`last_status` columns on `Agent` but they're not updated from the WS loop — live state lives only in the registry (by design, but the DB columns are dead weight).
- No Alembic / migrations — `create_all` only. Schema change = DB drop or manual SQL.
- Session cookie secret defaults to `"dev-secret-change-in-production"`. Production `.env.production` sets a real one; don't ship without it.
- Session secret in `.env.production` is committed-adjacent (in working tree, gitignored); shoulder-check before sharing screenshots.
- MVP caps are hardcoded constants in two files (`MAX_USERS=5` in `http_routes.py`, `MAX_AGENTS_PER_USER=5` and `MAX_ACTIVE_TOKENS_PER_AGENT=1` in `agent_routes.py`); marked TODO.
- No rate limiting on HTTP endpoints (login especially).
- `_deny` returns an HTTP response via raw ASGI to signal pre-accept 401 — nonobvious but correct for Starlette.

## 6. Web — detail

**Stack:** React 18, TS strict, Vite 6, Tailwind v3, TanStack Query v5, React Router v6.

**Structure** (`web/src/`):
- `api/` — typed fetch wrappers (`client.ts` with `ApiError`/`UnauthorizedError`), `auth.ts`, `agents.ts`, `tokens.ts`, `viewer.ts`.
- `hooks/` — `useCurrentUser`, `useAgents` (+`useDeleteAgent`), `useAgentStatus` (10s refetch), `useAgentTokens`, `useViewerStream`.
- `pages/` — Home (redirect), Login, Agents, AgentLive, AgentTokens, AgentConnectGuide, NotFound.
- `components/` — `AppShell`, `ProtectedRoute`, `AgentStatusBadge`, `ViewerConnectionBadge`, `WaterfallCanvas`.
- `utils/fft.ts` — base64 → Float32Array decode + dBFS normalization.
- `types/` — API + WS message shapes mirrored from server Pydantic/codec.
- Global 401 handler in `main.tsx` (`QueryCache.onError` → `window.location.replace("/login")`).

**Waterfall rendering:** uses vendored `@hony2323/waterfall-canvas` (`web/vendor/`). Frames pushed imperatively via `ref.current.push()` — no React state on the hot path. Config_version mismatches dropped.

**Viewer WS hook (`useViewerStream`)** is the trickiest piece: dual refs (`wsRef` stale-guard, `retryEnabledRef` to distinguish permanent errors from transient), exponential backoff capped at 30s, resets count on `subscribe_ack`, nulls `onclose` before `close()` to prevent spurious reconnect on unmount.

**Deployment:** `vercel.json` present; `@vercel/speed-insights` installed.

**Web gaps (per `web_mvp_status.md` + `web_production_notes.md`):**
- No ESLint in CI; only typecheck + build.
- No tests.
- No agent search/sort on dashboard, no last-seen summaries.
- No account settings page beyond signup/login/delete.

## 7. Protocol v0.3 — reminders

- Handshake is strict: HTTP Upgrade → `connect` → `stream_config` → frames; violations return `NO_STREAM_CONFIG` / `PROTOCOL_MISMATCH` / `UNSUPPORTED_ENCODING`.
- `AUTH_FAILED` is always HTTP 401 on Upgrade; never a WS message.
- Only `json_base64`; `binary_ws` partial implementation in agent codec but post-MVP — do not wire.
- Identity layering: `node_id` (stable) vs `session_id` (per-connection) vs `stream_id` (always `"default"` in MVP) vs `config_version` (server-assigned, monotonic per stream) vs `frame_index` (resets on config change).
- Tests **must** assert on decoded float values, never base64 strings.

## 8. Deployment

- **Server:** DigitalOcean droplet, `docker compose up -d --build` with `server/Dockerfile` + Caddy 2 reverse proxy. Domain `api.rf-platform.xyz`. Bootstrap user via `docker compose run --rm server python -m server.app.bootstrap ...`. Persistent DB at `/mnt/data/rf_platform.db`.
- **Web:** Vercel (`vercel.json`). CORS origins `https://rf-platform.xyz,https://rf-platform.vercel.app`.
- **CI:** three separate workflows, path-filtered: agent typecheck+tests, server typecheck+tests, web typecheck+build.

## 9. Suggested next steps

Ordered by impact:

1. **Real SDR hardware source for the agent.** This is the headline gap — the three existing sources are dev-only. `pyrtlsdr` is already an optional dep. Next pragmatic step: an `RTLSDRSource` implementing `IQSource`, wired into CLI via `--device rtl-sdr`. Requires solving live capture-timestamp propagation (currently timestamped at dequeue).
2. **Graceful agent shutdown.** SIGTERM/SIGINT hook that drains in-flight frames, sends a final `agent_status`, closes the WS cleanly. Needed for any production deployment and for Docker stop signals.
3. **`CONFIG_REJECTED` + `RATE_EXCEEDED` handling in the agent session.** Today both are silently swallowed. `CONFIG_REJECTED` should raise `SessionError`; `RATE_EXCEEDED` should flip the bandwidth limiter into drop mode.
4. **Server: Alembic migrations.** `create_all` is a time bomb once there's a real DB. Introduce now while the schema is small.
5. **Server: rate-limit `/auth/login` and `/auth/signup`.** `slowapi` or similar. MVP has 5-user cap, but there's no per-IP throttle on password guessing.
6. **Server: persist agent last-seen / last-status to DB columns already defined on `Agent`.** Either use them or drop them — currently schema debt.
7. **Server: delete dead state sooner.** Consider `user_id` session invalidation on password change / account delete (today: cookie still valid until expiry if stolen, since there's no server-side session store).
8. **Web: ESLint in CI, basic test harness** (Vitest + React Testing Library for `useViewerStream` reconnect logic especially).
9. **Web: agent list polish** — search/sort, last-seen column, bulk token revoke.
10. **Observability.** Structured JSON logs on the server, request IDs, a metrics endpoint (even just Prometheus-format counters for session counts, frames/sec, rejected frames).
11. **Move MVP limits into config** (`RF_MAX_USERS`, `RF_MAX_AGENTS_PER_USER`, `RF_MAX_TOKENS_PER_AGENT`) so prod can override without a recompile.
12. **Protocol follow-ups (post-MVP, not now):** `binary_ws`, `epoch_ms`, `data.psd_db`, multi-`stream_id` for multi-tuner. Listed in the protocol doc as deferred — don't pull forward without a version bump.
