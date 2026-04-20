# Web — MVP Status

**Date:** 2026-04-20  
**Plan:** `docs/web_mvp_claude_plan.md`  
**Protocol version:** 0.3 (frozen)

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
| `vite.config.ts` | Dev server with `bypass()`-guarded proxy to `localhost:8000`; WebSocket proxy included |
| `tsconfig.json` / `tsconfig.app.json` / `tsconfig.node.json` | Strict TypeScript, composite build, ES2020 target |
| `tailwind.config.js` / `postcss.config.js` | Tailwind content paths wired to `src/` |
| `src/index.css` | Tailwind base/components/utilities directives |
| `src/vite-env.d.ts` | `/// <reference types="vite/client" />` — required for CSS import typing |
| `src/main.tsx` | App entry: `StrictMode` + `QueryClientProvider` + `RouterProvider` |
| `src/app/App.tsx` | Thin `RouterProvider` wrapper |
| `src/app/router.tsx` | Stub router — catch-all `*` route only; real routes added in Phase 4 |
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

## What does not exist yet

### Phase 3 — API client (not started)

`src/api/client.ts`, `auth.ts`, `agents.ts`, `tokens.ts`

- Base fetch wrapper with `credentials: "include"`, typed `ApiError`, `UnauthorizedError` (401)
- `login()`, `logout()`, `getMe()`
- `getAgents()`, `getAgent(id)`, `getAgentStatus(id)`
- `getAgentTokens(agentId)`, `createAgentToken(agentId, label)`, `revokeAgentToken(agentId, tokenId)`

### Phase 4 — Auth flow (not started)

`LoginPage.tsx`, `ProtectedRoute.tsx`, `hooks/useCurrentUser.ts`, updated `router.tsx`

- TanStack Query hook wrapping `getMe()`
- `ProtectedRoute`: loading spinner → unauthenticated redirect → render children
- `LoginPage`: email/password form, error display on 401
- Real routes: `/login`, `/agents`, `/agents/:id/live`, `/agents/:id/tokens`, `*` → `NotFoundPage`

### Phase 5 — Agents list page (not started)

`AgentsPage.tsx`, `hooks/useAgents.ts`, `hooks/useAgentStatus.ts`

- TanStack Query wrapping `getAgents()` and `getAgentStatus(id)` (10s refetch)
- Inline agent list rows with status badge; links to live and token pages

### Phase 6 — Token management page (not started)

`AgentTokensPage.tsx`, `hooks/useAgentTokens.ts`

- Token table with revoke button
- Create token dialog: label input → display raw token once with copy button

### Phase 7 — Viewer WebSocket hook (not started)

`hooks/useViewerStream.ts`

- Plain `useEffect` + `useRef` + `useState` (no TanStack Query)
- States: `idle → connecting → subscribed → offline | error`
- Auto-reconnect with exponential backoff (1s → 30s); resets on successful ack
- Frame callbacks stored in a `Set` ref — no React state per frame
- Config stored in both ref (for frame callbacks) and state (for UI re-render)

### Phase 8 — Waterfall canvas (not started)

`components/WaterfallCanvas.tsx`, `utils/fft.ts`

- `decodeFloat32Payload(base64, expectedBinCount): Float32Array | null`
- Imperative canvas: scroll-down per frame, new row written as `ImageData` at `y=0`
- Clamp bins to `[-120, 0]` dBFS → 0–255 grayscale
- Config change → full canvas reset (width = `bin_count`, height = 400, black fill)

### Phase 9 — Live page (not started)

`AgentLivePage.tsx`, `components/ViewerConnectionBadge.tsx`

- Top bar: agent name, status badge, WS connection badge
- Waterfall fills available width
- Error/offline message panel when not subscribed

### Phase 10 — Error state polish (not started)

- 401 anywhere → redirect to `/login`
- 404 on agent page → "Agent not found"
- 5xx → "Server error, try again"
- WS `FORBIDDEN` → access error message
- WS `AGENT_OFFLINE` → offline state UI
- WS socket close before ack → "Connection failed"

---

## Gaps

### Gap: `router.tsx` is a stub

The router has only a `*` catch-all route. All real routes (`/login`, `/agents`, etc.) are added in Phase 4.

### Gap: No lint step in CI

The web CI runs only `typecheck` and `build`. ESLint is not configured yet. Mirror server CI lint step when ESLint is added.

### Gap: `vite.config.ts` proxy `bypass()` may miss edge cases

The bypass list is a static extension check. If Vite adds new internal URL patterns (e.g. `/__vite_ping`) they would be incorrectly proxied to the backend. Low risk for MVP but worth revisiting if proxy issues appear during development.

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
