# Web — MVP Status

**Date:** 2026-04-30
**Plan:** `docs/web_mvp_claude_plan.md`
**Viewer contract version:** 0.4 — viewer `spectrum_frame` is consumed as a binary WebSocket message (`useViewerStream` sets `binaryType = "arraybuffer"`, parses a uint16 header_len + JSON header + raw float32 LE payload via `Float32Array` view).

This document describes what is implemented in the web frontend, what is stubbed or hollow, and what is missing before the web client can be considered production-ready for MVP.

---

## Data flow

```
Browser
  → Vite dev proxy (localhost:5173 → localhost:8000)
  → HTTP: fetch() with credentials: "include" (session cookie)
  → WS: WebSocket("/ws/viewer") → subscribe → stream_config → spectrum_frame[]
  → WaterfallCanvas (imperative canvas, no React state per frame)
```

---

## What exists

### Project scaffold (Phase 1 — complete)

| File | Purpose |
|---|---|
| `package.json` | All deps: React 18, React Router v6, TanStack Query v5, Tailwind CSS v3, Vite 6, TypeScript 5 |
| `vite.config.ts` | Dev server with explicit path proxies (`/ws`, `/auth`, `/agents`, `/me`) to `localhost:8000`; only `/ws` gets `ws: true` |
| `tsconfig.json` / `tsconfig.app.json` / `tsconfig.node.json` | Strict TypeScript, composite build, ES2020 target |
| `tailwind.config.js` / `postcss.config.js` | Tailwind content paths wired to `src/` |
| `src/index.css` | Tailwind base/components/utilities directives |
| `src/vite-env.d.ts` | `/// <reference types="vite/client" />` — required for CSS import typing |
| `src/main.tsx` | App entry: `StrictMode` + `QueryClientProvider` + `RouterProvider` |
| `src/app/App.tsx` | Thin `RouterProvider` wrapper |
| `.gitignore` | Node/Vite-specific: `node_modules/`, `dist/`, `.vite/`, `*.tsbuildinfo` |
| `.github/workflows/web-ci.yml` | CI: typecheck (`tsc -b --noEmit`) + build (`npm run build`); path-filtered to `web/**` |

---

### Types (Phase 2 — complete)

#### `src/types/api.ts`

HTTP request/response shapes derived directly from server Pydantic models:

| Type | Source |
|---|---|
| `LoginRequest` | `POST /auth/login` body |
| `UserResponse` | `GET /me`, `POST /auth/login` response |
| `AgentResponse` | `GET /agents`, `GET /agents/:id` response |
| `AgentStatusResponse` | `GET /agents/:id/status` response |
| `TokenCreateRequest` | `POST /agents/:id/tokens` body |
| `TokenResponse` | `GET /agents/:id/tokens[]`, revoke response |
| `TokenCreateResponse` | `POST /agents/:id/tokens` response (extends `TokenResponse` with `token: string`) |

#### `src/types/viewer.ts`

WebSocket wire message types derived from server codec (`protocol/codec.py`):

| Type | Direction | Notes |
|---|---|---|
| `ViewerSubscribeMessage` | outbound | `{ msg_type: "subscribe", agent_id }` |
| `ViewerSubscribeAckMessage` | inbound | `subscribe_ack` from server |
| `ViewerStreamConfigMessage` | inbound | Full RF config; `rf.bin_count` is authoritative for payload size |
| `ViewerSpectrumFrameMessage` | inbound | `data.payload` is base64 float32 LE |
| `ViewerErrorMessage` | inbound | `code` + `message`; known codes: `AGENT_OFFLINE`, `FORBIDDEN`, `INVALID_FRAME`, `INTERNAL_ERROR` |
| `ViewerInboundMessage` | — | Discriminated union of all four inbound types |
| `RfConfig` | — | Nested in `ViewerStreamConfigMessage` |
| `FftSemantics` | — | Nested in `ViewerStreamConfigMessage` |

---

### API client (Phase 3 — complete)

#### `src/api/client.ts`

| Export | Purpose |
|---|---|
| `ApiError` | Non-2xx response; carries `status: number` and `message: string` |
| `UnauthorizedError` | Extends `ApiError`; thrown on 401; callers redirect to `/login` |
| `apiFetch<T>` | Base wrapper: `credentials: "include"`, JSON encode/decode, 204 → `undefined` |

#### `src/api/auth.ts`

| Function | Route |
|---|---|
| `login(email, password)` | `POST /auth/login` → `UserResponse` |
| `logout()` | `POST /auth/logout` → void (204) |
| `getMe()` | `GET /me` → `UserResponse` |

#### `src/api/agents.ts`

| Function | Route |
|---|---|
| `getAgents()` | `GET /agents` → `AgentResponse[]` |
| `getAgent(id)` | `GET /agents/:id` → `AgentResponse` |
| `getAgentStatus(id)` | `GET /agents/:id/status` → `AgentStatusResponse` |

#### `src/api/tokens.ts`

| Function | Route |
|---|---|
| `getAgentTokens(agentId)` | `GET /agents/:id/tokens` → `TokenResponse[]` |
| `createAgentToken(agentId, label)` | `POST /agents/:id/tokens` → `TokenCreateResponse` (201) |
| `revokeAgentToken(agentId, tokenId)` | `POST /agents/:id/tokens/:tokenId/revoke` → `TokenResponse` |

---

### Auth flow (Phase 4 — complete; Phase 11 additions noted)

| File | Purpose |
|---|---|
| `src/pages/LoginPage.tsx` | Email/password form + Google Sign-In button (conditional on `VITE_GOOGLE_CLIENT_ID`); signup mode shows confirm-password field and live password-strength checklist (min-8, uppercase, lowercase, digit); 401 → inline error; success → navigate to `/agents` |
| `src/components/ProtectedRoute.tsx` | Calls `getMe()`; loading spinner → unauthenticated redirect to `/login` → render children |
| `src/hooks/useCurrentUser.ts` | TanStack Query hook wrapping `getMe()`; throws `UnauthorizedError` on 401 |
| `src/pages/NotFoundPage.tsx` | Catch-all 404 page |
| `src/app/router.tsx` | Routes: `/login`, `/agents`, `/agents/:agentId/live` (stub), `/agents/:agentId/tokens`, `*` → NotFoundPage |
| `src/api/auth.ts` | Added `loginWithGoogle(token)` → `POST /auth/google` |
| `src/types/api.ts` | Added `GoogleAuthRequest` |
| `index.html` | Loads `https://accounts.google.com/gsi/client` async/defer |
| `src/vite-env.d.ts` | Added `VITE_GOOGLE_CLIENT_ID` env type + minimal Google GSI window type declarations |

---

### Agents list page (Phase 5 — complete)

| File | Purpose |
|---|---|
| `src/pages/AgentsPage.tsx` | Lists all agents with name, node_id, status badge, links to live/tokens pages |
| `src/hooks/useAgents.ts` | TanStack Query wrapping `getAgents()` |
| `src/hooks/useAgentStatus.ts` | TanStack Query wrapping `getAgentStatus(id)`; 10s refetch interval |
| `src/components/AgentStatusBadge.tsx` | Green "Online" / gray "Offline" badge driven by `AgentStatusResponse` |

---

### Token management page (Phase 6 — complete)

| File | Purpose |
|---|---|
| `src/pages/AgentTokensPage.tsx` | Token table with revoke button; hosts `CreateTokenDialog` |
| `src/hooks/useAgentTokens.ts` | TanStack Query hooks: `useAgentTokens`, `useCreateAgentToken`, `useRevokeAgentToken` |

`CreateTokenDialog`: label input → on success, displays raw token once with copy button and "Copy now" warning.

---

### Viewer WebSocket hook (Phase 7 — complete)

| File | Purpose |
|---|---|
| `src/api/viewer.ts` | `viewerWsUrl()` returns `"/ws/viewer"` (relative; Vite proxy upgrades to WS) |
| `src/hooks/useViewerStream.ts` | Full WS lifecycle hook |

Hook details:
- States: `idle → connecting → subscribed → offline | error`
- `retryEnabledRef`: set `true` on `subscribe_ack`; `false` on `FORBIDDEN`/`INVALID_FRAME` — controls reconnect
- `gotServerErrorRef`: prevents duplicate generic error message when server already described the failure
- Exponential backoff: `Math.min(1000 * 2^n, 30000)` ms; resets to 0 on successful `subscribe_ack`
- Stale socket guard (`wsRef.current !== ws`) in all four WS event handlers
- Frame callbacks stored in a `Set` ref — no React state per frame
- Config stored in both `configRef` (imperative) and `setConfig` (UI re-render)
- Cleanup: nulls `onclose` before `close()` to prevent spurious reconnect on unmount

---

### Waterfall canvas (Phase 8 — complete)

| File | Purpose |
|---|---|
| `src/utils/fft.ts` | Frame decode + conversion helpers |
| `src/components/WaterfallCanvas.tsx` | Waterfall component wrapping `@hony2323/waterfall-canvas` |

**`@hony2323/waterfall-canvas@0.1.6`** (GitHub Packages) — ring-buffer renderer with zoom/pan, tooltip, time bar, pluggable colormaps, full TypeScript types.

`utils/fft.ts` exports:
- `decodeFloat32Payload(base64, expectedBinCount)`: `atob` → `Uint8Array` → `Float32Array` (LE); validates byte length; returns `null` and logs in dev on mismatch
- `toWaterfallFrame(frame, config)`: decodes payload, normalizes dBFS `[-120, 0]` → `[0, 1]`, returns `ParsedFrame` for the renderer
- `freqFormat` / `valueFormat`: module-level stable refs (avoids renderer recreation on re-render)

`WaterfallCanvas`:
- Uses `WaterfallCanvas` from `@hony2323/waterfall-canvas/react` with turbo colormap, tooltip, time bar
- Frames pushed imperatively via `ref.current.push()` — no React re-renders on the hot path
- Drops frame if `config_version` doesn't match current config
- Renders placeholder div while awaiting first `stream_config`

---

### Live page (Phase 9 — complete)

| File | Purpose |
|---|---|
| `src/pages/AgentLivePage.tsx` | Full live viewer page |
| `src/components/ViewerConnectionBadge.tsx` | WS state badge: idle / connecting / live / offline / error |

`AgentLivePage`:
- Top bar: back link, agent name, `AgentStatusBadge`, `ViewerConnectionBadge`
- `useViewerStream(agentId)` drives WS lifecycle
- `WaterfallCanvas` fills the page; placeholder shown until first `stream_config`
- Error/offline panel beneath waterfall when `connectionState` is `error` or `offline`
- Agent name fetched via `getAgent(agentId)` (TanStack Query); falls back to `agentId` while loading

`ViewerConnectionBadge` renders a colored pill for each of the five `ViewerConnectionState` values.

---

### Error state polish (Phase 10 — complete)

| Location | Condition | Behaviour |
|---|---|---|
| `src/main.tsx` `QueryCache.onError` | Any query throws `UnauthorizedError` (401) | `window.location.replace("/login")` |
| `AgentLivePage` `agentQuery` | `ApiError` with status 404 | "Agent not found." full-screen message |
| `AgentLivePage` `agentQuery` | Any other error | "Server error. Please try again." full-screen message |
| `useViewerStream` `ws.onmessage` | `error` message with code `FORBIDDEN` | `connectionState = "error"`, `lastError = msg.message`; no reconnect |
| `useViewerStream` `ws.onmessage` | `error` message with code `AGENT_OFFLINE` | `connectionState = "offline"`, `lastError = msg.message` |
| `useViewerStream` `ws.onclose` | Closed before `subscribe_ack` | `connectionState = "error"`, `lastError = "Connection closed before subscribe was acknowledged"` |

WS error states (FORBIDDEN, AGENT_OFFLINE, close-before-ack) were already handled in Phase 7; Phase 10 added the HTTP-layer guards.

---

## What does not exist yet

---

## Gaps

### Gap: No lint step in CI

The web CI runs only `typecheck` and `build`. ESLint is not configured yet. Mirror server CI lint step when ESLint is added.

---

## Post-MVP (do not implement)

Per `CLAUDE.md` and protocol v0.3:

- `binary_ws` / `msgpack` wire encoding support on the viewer side
- `epoch_ms` timestamp display
- `data.phase_rad`, `data.psd_db` visualization
- `stream_id` beyond `"default"` (multi-agent waterfall)
- Color maps beyond grayscale (heatmap, viridis)
- Zoom / frequency axis overlay on waterfall
- Agent creation / deletion UI (server has the routes; not in MVP plan)
