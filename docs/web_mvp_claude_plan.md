# Web MVP Implementation Plan

## Context

The RF Platform backend (server on port 8000) is complete and frozen. The `web/` directory is an empty placeholder (bare `package.json`, empty `src/` and `scripts/` dirs). This plan builds the full browser UI from scratch, following `docs/web_mvp_plan.md` exactly.

User choices: **Tailwind CSS** + **TanStack Query**.

---

## Key Findings from Exploration

- Session cookie name: `session` (server default, `RF_SESSION_COOKIE_NAME` env)
- Cookie is HTTPOnly → browser sends automatically with `credentials: "include"`
- No CORS configured on server → solved by Vite dev proxy (no backend change needed)
- WebSocket at `/ws/viewer` requires same `session` cookie → also proxied via Vite
- Server port: `8000`
- Token on creation: 64 hex chars returned once, stored as SHA256 hash

---

## Critical Files

- `web/package.json` — current empty placeholder, will be replaced
- `web/vite.config.ts` — new, needs proxy to `http://localhost:8000`
- `web/src/` — all new frontend code
- `server/src/server/app/api.py` — DO NOT modify (no backend changes needed)

---

## Folder Structure to Create

Start with the minimum needed to reach a working waterfall. Extract components only when the core flow is working.

```
web/
  index.html
  vite.config.ts
  tsconfig.json
  tsconfig.node.json
  tailwind.config.js
  postcss.config.js
  src/
    main.tsx
    index.css
    app/
      App.tsx
      router.tsx
    api/
      client.ts
      auth.ts
      agents.ts
      tokens.ts
    pages/
      LoginPage.tsx       ← inline form, no sub-components
      AgentsPage.tsx      ← inline list rows + status badge
      AgentLivePage.tsx   ← inline status + inline waterfall wiring
      AgentTokensPage.tsx ← inline token table + create dialog
      NotFoundPage.tsx
    components/
      ProtectedRoute.tsx  ← needed early for routing
      WaterfallCanvas.tsx ← extracted because imperative canvas logic is large
    hooks/
      useCurrentUser.ts
      useAgents.ts
      useAgentStatus.ts
      useAgentTokens.ts
      useViewerStream.ts
    types/
      api.ts
      viewer.ts
    utils/
      fft.ts
```

Defer extracting `AgentList`, `AgentStatusBadge`, `TokenPanel`, `CreateTokenDialog`, `ViewerConnectionBadge` into separate files until after the full live-page flow works end-to-end.

---

## Phase-by-Phase Plan

### Phase 1 — Project Bootstrap

**Files to create/modify:**
- `web/package.json` — add all dependencies
- `web/index.html` — Vite entry HTML
- `web/vite.config.ts` — Vite + React + Tailwind, proxy `/api`, `/ws`, `/auth`, `/agents`, `/me` to `http://localhost:8000`
- `web/tsconfig.json` + `web/tsconfig.node.json`
- `web/tailwind.config.js` + `web/postcss.config.js`
- `web/src/main.tsx` — mounts App + QueryClientProvider + RouterProvider
- `web/src/index.css` — Tailwind directives

**Dependencies:**
```
react react-dom react-router-dom
@tanstack/react-query
tailwindcss @tailwindcss/vite (or postcss)
typescript @types/react @types/react-dom
vite @vitejs/plugin-react
```

**Vite proxy config** — single catch-all (avoids CORS, proxies WS too):
```ts
proxy: {
  '/': { target: 'http://localhost:8000', changeOrigin: true, ws: true },
}
```
Note: Vite serves its own assets (JS/CSS/HTML) before the proxy rule is checked, so this does not shadow the frontend bundle.

**Done when:** `npm run dev` starts, blank page loads at `http://localhost:5173`.

---

### Phase 2 — TypeScript Types

**Files:** `web/src/types/api.ts`, `web/src/types/viewer.ts`

**HTTP types** (from server API contract):
```ts
UserResponse, AgentResponse, AgentStatusResponse,
TokenResponse, TokenCreateResponse,
LoginRequest, TokenCreateRequest
```

**Viewer WS message types** (discriminated union on `msg_type`):
```ts
ViewerSubscribeMessage, ViewerSubscribeAckMessage,
ViewerStreamConfigMessage, ViewerSpectrumFrameMessage, ViewerErrorMessage
ViewerInboundMessage = ViewerSubscribeAckMessage | ViewerStreamConfigMessage | ViewerSpectrumFrameMessage | ViewerErrorMessage
```

No `any` allowed anywhere.

---

### Phase 3 — API Client + HTTP Layer

**Files:** `web/src/api/client.ts`, `auth.ts`, `agents.ts`, `tokens.ts`

**`client.ts`:**
- Base fetch wrapper with `credentials: "include"`, JSON encode/decode
- Throws typed `ApiError` (status + message) for non-2xx
- On 401: throws specific `UnauthorizedError` (callers can redirect)

**`auth.ts`:** `login(email, password)`, `logout()`, `getMe()`

**`agents.ts`:** `getAgents()`, `getAgent(id)`, `getAgentStatus(id)`

**`tokens.ts`:** `getAgentTokens(agentId)`, `createAgentToken(agentId, label)`, `revokeAgentToken(agentId, tokenId)`

---

### Phase 4 — Auth Flow

**Files:** `LoginPage.tsx`, `ProtectedRoute.tsx`, `hooks/useCurrentUser.ts`, `app/router.tsx`, `app/App.tsx`

**`useCurrentUser`:** TanStack Query wrapping `getMe()`. Returns `{ user, isLoading, isError }`.

**`ProtectedRoute`:** Reads `useCurrentUser`. If loading → spinner. If unauthenticated → redirect to `/login`. Otherwise → render children.

**`LoginPage`:** Email + password form. On submit: `login()` → navigate to `/agents`. On 401: show "Invalid credentials".

**`router.tsx`:** React Router `createBrowserRouter`:
- `/login` → `LoginPage` (public)
- `/agents` → `ProtectedRoute` → `AgentsPage`
- `/agents/:agentId/live` → `ProtectedRoute` → `AgentLivePage`
- `/agents/:agentId/tokens` → `ProtectedRoute` → `AgentTokensPage`
- `*` → `NotFoundPage`

**Done when:** Login redirects to `/agents`, unauthenticated routes redirect to `/login`, logout clears session.

---

### Phase 5 — Agents List Page

**Files:** `AgentsPage.tsx`, `components/AgentList.tsx`, `components/AgentStatusBadge.tsx`, `hooks/useAgents.ts`, `hooks/useAgentStatus.ts`

**`useAgents`:** TanStack Query wrapping `getAgents()`.

**`useAgentStatus(agentId)`:** TanStack Query wrapping `getAgentStatus(agentId)`, refetch interval 10s.

**`AgentList`:** Renders one row per agent: name, `stable_node_id`, `AgentStatusBadge`, link to `/agents/:id/live`, link to `/agents/:id/tokens`.

**`AgentStatusBadge`:** Green "Online" / Gray "Offline" pill.

**Done when:** Page lists all agents with live status badges, links work.

---

### Phase 6 — Token Management Page

**Files:** `AgentTokensPage.tsx`, `components/TokenPanel.tsx`, `components/CreateTokenDialog.tsx`, `hooks/useAgentTokens.ts`

**`useAgentTokens(agentId)`:** TanStack Query wrapping `getAgentTokens()`.

**`TokenPanel`:** Table of tokens (label, created_at, revoke button). "Create Token" button opens `CreateTokenDialog`.

**`CreateTokenDialog`:** Label input → on submit: call `createAgentToken()` → display raw token with "Copy" button + bold warning "This token will not be shown again." → invalidate token list query on close.

**Revoke:** Calls `revokeAgentToken()` → invalidates query.

**Done when:** User can mint and copy token once, revoke tokens, list refreshes.

---

### Phase 7 — Viewer WebSocket Hook

**File:** `hooks/useViewerStream.ts`

**Behavior:**
1. Open `WebSocket` to `/ws/viewer` (relative URL, proxied)
2. On open: send `{ msg_type: "subscribe", agent_id }`
3. Parse incoming JSON frames as `ViewerInboundMessage`
4. Update `connectionState` React state: `idle → connecting → subscribed → offline | error`
5. On `subscribe_ack`: set state to `"subscribed"`
6. On `stream_config`: store config in **both** a `useRef` (for frame callbacks to read synchronously) and React state (to trigger re-render for UI display)
7. On `spectrum_frame`: call registered frame callbacks; callbacks read config from the ref, not state, so they always see the latest value without stale closure issues
8. On `error` (AGENT_OFFLINE → `"offline"`, others → `"error"`): set `lastError`, update state; close socket
9. On unexpected socket close: set state to `"error"`, schedule auto-reconnect (1s initial, up to 30s backoff, reset on successful subscribe_ack); cancel retry on unmount
10. Cleanup: close socket + cancel any pending reconnect timer on unmount or `agentId` change

**Return shape:**
```ts
{
  connectionState: "idle" | "connecting" | "subscribed" | "offline" | "error",
  config: ViewerStreamConfigMessage | null,  // from React state — drives UI
  lastError: string | null,
  onFrame: (cb: FrameCallback) => UnsubscribeFn
}
```

Frame callbacks registered via `onFrame` are stored in a `Set` held in a ref — no React state updates per frame. Callbacks close over the config ref so they always read the latest config safely.

**TanStack Query is NOT used here.** WebSocket lifecycle is managed by plain `useEffect` + `useRef` + `useState`.

**Done when:** Browser connects, handles all message types, auto-reconnects on drop, cleans up on unmount.

---

### Phase 8 — Waterfall Canvas

**Files:** `components/WaterfallCanvas.tsx`, `utils/fft.ts`

**`fft.ts`:**
```ts
// Returns null on length mismatch — caller drops the frame
function decodeFloat32Payload(base64: string, expectedBinCount: number): Float32Array | null
```
- `atob(base64)` → `Uint8Array` → `new Float32Array(uint8.buffer)` (native little-endian float32)
- Validates `result.length === expectedBinCount`; if mismatch logs a dev warning and returns `null`
- Structured so a buffer-reuse optimisation (pre-allocated `Float32Array`) can be dropped in later by changing only this function's internals

**`WaterfallCanvas`:**
- Props: `config: ViewerStreamConfigMessage | null`, `onFrame: (cb: FrameCallback) => UnsubscribeFn`
- Uses `useRef<HTMLCanvasElement>`
- On config change (tracked via `useEffect` on `config`): **fully reset** — set `canvas.width = config.rf.bin_count`, `canvas.height = 400`, clear to black, set `ctx.imageSmoothingEnabled = false`
- Registers frame callback on mount; the callback reads `config` from a ref so it is always current
- Per frame (imperative, no React state):
  1. Guard: skip if no config in ref
  2. `decodeFloat32Payload(frame.data.payload, config.rf.bin_count)` — drop frame if null
  3. Clamp each bin to `[-120, 0]` dBFS, map to `0..255` grayscale
  4. `ctx.drawImage(canvas, 0, 1)` to scroll existing image down one row
  5. Write new row as `ImageData` at `y=0`
- `imageSmoothingEnabled = false` set once after each canvas resize (not per-frame)

**Done when:** Frames render as scrolling grayscale waterfall; config change resets canvas cleanly.

---

### Phase 9 — Live Page

**File:** `AgentLivePage.tsx`, `components/ViewerConnectionBadge.tsx`

**Layout:**
- Top bar: agent name, `AgentStatusBadge`, `ViewerConnectionBadge` (WS state)
- Session info: `session_id`, last heartbeat (if online)
- Waterfall canvas fills available width
- Error/offline message panel when `connectionState` is not `subscribed`

**`ViewerConnectionBadge`:** Colored dot + text for each `connectionState`.

**Behavior:**
- Fetches `getAgentStatus` (TanStack Query, 10s refetch)
- If agent offline before subscribe: show "Agent is offline" — still attempt WS (server sends `AGENT_OFFLINE` error)
- `useViewerStream(agentId)` drives connection state
- `WaterfallCanvas` receives stream config + frame callback

**Done when:** Full path — login → agents → live page → waterfall renders.

---

### Phase 10 — Error States Polish

Ensure all required error cases are visible:
- 401 anywhere → redirect to login (handled in `client.ts`)
- 404 on agent page → "Agent not found"
- 5xx → "Server error, try again"
- WS `FORBIDDEN` → access error message
- WS `AGENT_OFFLINE` → offline state UI
- WS socket close before ack → "Connection failed"

---

## One Backend Change Required

**Problem:** The Vite dev proxy handles `/auth`, `/agents`, `/me`, `/ws` but the server's `/ws/viewer` sends cookies. The proxy handles this transparently.

**No backend code changes needed.** The Vite proxy fully eliminates the CORS problem for development.

---

## Verification

1. `cd web && npm install && npm run dev` — app starts on port 5173
2. Start server: `cd server && uvicorn "server.app.api:create_app" --factory --reload`
3. Navigate to `http://localhost:5173` → redirects to `/login`
4. Log in with bootstrapped credentials → lands on `/agents`
5. Agents list shows with status badges
6. Create token → copy once → revoke
7. Open live page → if agent online: waterfall scrolls
8. Stop agent → UI transitions to offline state
9. `npm run build` completes without TypeScript errors

---

## Implementation Order

1. Bootstrap (package.json, vite config, tsconfig, tailwind, index.html, main.tsx)
2. Types (api.ts, viewer.ts)
3. API client (client.ts, auth.ts, agents.ts, tokens.ts)
4. Auth flow (LoginPage, ProtectedRoute, useCurrentUser, router)
5. Agents page (AgentsPage, AgentList, AgentStatusBadge, useAgents, useAgentStatus)
6. Tokens page (AgentTokensPage, TokenPanel, CreateTokenDialog, useAgentTokens)
7. Viewer hook (useViewerStream)
8. Waterfall canvas (WaterfallCanvas, fft.ts)
9. Live page (AgentLivePage, ViewerConnectionBadge)
10. Error state polish + full manual verification
