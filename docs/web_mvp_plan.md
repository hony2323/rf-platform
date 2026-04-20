# RF Platform — Web MVP Plan

This document is the implementation plan for the **web app MVP**.
It is written for a coding agent.

The backend MVP is already complete. The goal now is to build the **smallest useful web UI** on top of the frozen server contract.

Do **not** redesign the backend API. Do **not** add backend features unless the web app is blocked without them.

---

## Goal

Build a minimal browser UI that lets a user:

1. log in
2. see their agents
3. inspect whether an agent is online
4. create and revoke agent tokens
5. open a live single-agent view
6. receive live FFT frames over the viewer WebSocket
7. render a spectrogram waterfall in the browser

This is the shortest path to a real end-to-end product demo.

---

## Non-goals

Do **not** implement these now:

- historical playback
- multi-user collaboration
- orgs / teams / permissions beyond current user ownership
- backend schema changes unless strictly required
- fancy dashboard analytics
- multi-agent simultaneous viewer layout
- mobile-first design
- design system overengineering
- Redux / global state complexity
- SSR / Next.js migration

Keep it simple.

---

## Source of truth

The web app must consume the frozen backend contract exactly as documented in:

- `docs/server_api_contract.md`

Relevant backend surfaces already exist:

- `POST /auth/login`
- `POST /auth/logout`
- `GET /me`
- `GET /agents`
- `GET /agents/{id}`
- `GET /agents/{id}/status`
- `GET /agents/{id}/tokens`
- `POST /agents/{id}/tokens`
- `POST /agents/{id}/tokens/{token_id}/revoke`
- `WS /ws/viewer`

The browser uses cookie auth for HTTP and viewer WebSocket.

---

## Product shape

The MVP web app should have exactly these screens:

### 1. Login page
Purpose:
- authenticate user via email/password

### 2. Agents page
Purpose:
- list all user agents
- show online/offline state
- link to live view
- link to token management

### 3. Agent detail / live view page
Purpose:
- show basic metadata
- show live session state
- connect to `WS /ws/viewer`
- subscribe to one agent
- render spectrogram waterfall

### 4. Agent tokens page or section
Purpose:
- list non-revoked tokens
- create token
- reveal raw token once
- revoke token

That is enough.

---

## Suggested tech stack

Use a boring stack.

- React
- TypeScript
- Vite
- React Router
- small local state via hooks
- canvas for waterfall rendering

Optional but acceptable:
- TanStack Query for HTTP fetching/caching
- a tiny CSS approach (plain CSS modules, Tailwind, or minimal utility CSS)

Avoid:
- Redux
- MobX
- complex UI frameworks unless already present

---

## Frontend architecture

Use this structure or something very close:

```text
web/
  src/
    app/
      router.tsx
      App.tsx
    api/
      client.ts
      auth.ts
      agents.ts
      tokens.ts
      viewer.ts
    pages/
      LoginPage.tsx
      AgentsPage.tsx
      AgentLivePage.tsx
      NotFoundPage.tsx
    components/
      ProtectedRoute.tsx
      AgentList.tsx
      AgentStatusBadge.tsx
      TokenPanel.tsx
      CreateTokenDialog.tsx
      WaterfallCanvas.tsx
      ViewerConnectionBadge.tsx
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
      base64.ts
      fft.ts
      time.ts
```

Do not mix raw fetch calls directly inside page JSX if it can be avoided.

---

## Data contracts to model in TypeScript

Create explicit TypeScript types for the backend contract.
Do not use `any`.

### HTTP types

```ts
UserResponse
AgentResponse
AgentStatusResponse
TokenResponse
TokenCreateResponse
LoginRequest
TokenCreateRequest
```

### Viewer WS message types

```ts
ViewerSubscribeMessage
ViewerSubscribeAckMessage
ViewerStreamConfigMessage
ViewerSpectrumFrameMessage
ViewerErrorMessage
```

Use discriminated unions by `msg_type`.

---

## Core implementation rules

### 1. Cookie auth only
Do not store auth tokens in localStorage.
Use browser cookies exactly as the backend expects.

### 2. One active viewer subscription per live page
The live page watches one agent only.
Do not implement multi-subscribe now.

### 3. Config-first rendering
The page must not attempt to decode/render spectrum frames before receiving `stream_config`.

### 4. Hot path stays light
Do not re-render the whole React tree for every frame.
Use a canvas component with imperative drawing.

### 5. Fail visibly
Handle these states clearly:
- not logged in
- agent offline
- WebSocket disconnected
- malformed/unexpected viewer message

### 6. Respect the frozen contract
Do not invent message types.
Do not rename fields.

---

## Waterfall rendering approach

The waterfall is the main product proof, so keep it simple and fast.

### MVP rendering model

- one HTML canvas
- each incoming frame is one row
- newest row appended at bottom or top (choose one and keep it consistent)
- previous rows shift visually
- amplitude mapped to grayscale or simple heatmap

### Initial constraints

- do not add zoom yet
- do not add pan yet
- do not add frequency markers yet
- do not add replay buffer yet

### Recommended drawing approach

For each frame:
1. base64-decode payload
2. interpret as little-endian float32 array
3. normalize values to a visual range
4. convert bins to pixel colors
5. shift existing image by 1 row
6. draw new row

### Performance note

Do not store all frames in React state.
Keep rendering state inside the canvas component or a dedicated imperative controller.

---

## Frame decoding

The viewer receives:

```json
{
  "msg_type": "spectrum_frame",
  "data": { "payload": "<base64 float32 LE>" }
}
```

Implement a helper:

```ts
function decodeFloat32Payload(base64Payload: string): Float32Array
```

Requirements:
- decode base64 correctly
- interpret bytes as float32 little-endian
- no silent truncation
- length should match `rf.bin_count`

If decoded length does not match expected bin count, drop the frame and surface a debug error in development.

---

## Suggested visual mapping

Start with a dead-simple amplitude mapping.

Example:
- expected dBFS range: `[-120, 0]`
- clamp values into that range
- map to `0..255`
- draw grayscale

This is enough for MVP.

You can swap to a nicer palette later.

---

## Routing plan

### `/login`
Public route.

### `/agents`
Protected route.
Shows all agents.

### `/agents/:agentId/live`
Protected route.
Shows single-agent live page.

Optional:
### `/agents/:agentId/tokens`
Protected route.
If you want fewer routes, token management can live inside the main live/detail page.

---

## HTTP layer plan

Create a small fetch wrapper.

### `api/client.ts`
Responsibilities:
- set `credentials: "include"`
- JSON encode/decode
- normalize non-2xx errors into typed exceptions

### `api/auth.ts`
Functions:
- `login(email, password)`
- `logout()`
- `getMe()`

### `api/agents.ts`
Functions:
- `getAgents()`
- `getAgent(agentId)`
- `getAgentStatus(agentId)`

### `api/tokens.ts`
Functions:
- `getAgentTokens(agentId)`
- `createAgentToken(agentId, label)`
- `revokeAgentToken(agentId, tokenId)`

---

## Viewer WebSocket hook

Implement `useViewerStream(agentId)`.

Responsibilities:
- open WebSocket to `/ws/viewer`
- send subscribe message after open
- receive `subscribe_ack`
- receive `stream_config`
- receive `spectrum_frame`
- receive `error`
- expose connection state
- expose latest config
- push frames to canvas callback without forcing full-page rerenders

Suggested returned shape:

```ts
{
  connectionState: "idle" | "connecting" | "subscribed" | "offline" | "error",
  config: ViewerStreamConfigMessage | null,
  lastError: string | null,
  onFrame: (cb: (frame: ViewerSpectrumFrameMessage) => void) => unsubscribeFn
}
```

Implementation note:
Do not keep an ever-growing array of frames in memory.

---

## UI behavior details

### Login page
- form: email, password
- submit to `POST /auth/login`
- on success: navigate to `/agents`
- on 401: show invalid credentials

### Agents page
For each agent show:
- name
- stable node id
- online/offline badge
- button/link to live page
- button/link to token management

Status source:
- simplest version: fetch `/agents`, then per row fetch `/agents/{id}/status`
- acceptable for MVP
- optional later optimization: status refresh loop or combined endpoint, but do not change backend now

### Live page
Show:
- agent name
- online/offline badge
- session id if online
- last heartbeat if available
- latest status JSON summary if useful
- waterfall canvas
- viewer connection state

Behavior:
- if offline before subscribe: show clear message
- if viewer gets `AGENT_OFFLINE`: show disconnected/offline state
- if stream config changes: reconfigure canvas immediately

### Token panel
Show:
- token list
- create token button
- revoke button

On create:
- show raw token once in a modal/panel
- strong warning: copy now, it will not be shown again

---

## Error handling

Handle these cases explicitly.

### HTTP
- 401 → redirect to `/login`
- 404 on agent page → show “agent not found”
- generic 5xx → show simple retry UI

### Viewer WebSocket
- `FORBIDDEN` → show access error
- `AGENT_OFFLINE` → show offline state
- `INVALID_FRAME` → show generic stream error
- socket close before subscribe ack → show connection failed

Do not silently ignore permanent failures.

---

## Phased implementation plan

---

## Phase 1 — Web app bootstrap

### Goal
Set up the web project skeleton with routing and HTTP client.

### Build
- Vite + React + TypeScript app
- router setup
- fetch client with cookie credentials
- API types
- placeholder pages

### Done when
- app runs locally
- `/login` and `/agents` routes exist
- API client is ready for use

---

## Phase 2 — Auth flow

### Goal
User can log in and access protected routes.

### Build
- login page form
- `getMe()` hook
- protected route wrapper
- logout action

### Rules
- no localStorage token auth
- auth state comes from backend cookie + `/me`

### Done when
- successful login redirects to `/agents`
- unauthenticated access to protected routes redirects to `/login`
- logout clears session and returns to login page

---

## Phase 3 — Agents list page

### Goal
User can see their agents and open live view.

### Build
- `GET /agents`
- per-agent status fetch via `GET /agents/{id}/status`
- list UI with status badge and links

### Done when
- page shows all agents
- online/offline status is visible
- clicking an agent opens live page

---

## Phase 4 — Token management UI

### Goal
User can mint and revoke tokens.

### Build
- token panel on agent page or dedicated route
- token list
- create token flow
- raw token once-only reveal UI
- revoke action

### Done when
- user can create token and copy it
- user can revoke token
- token list refreshes correctly

---

## Phase 5 — Viewer WebSocket client

### Goal
Browser can subscribe to one live agent.

### Build
- `useViewerStream(agentId)`
- subscribe flow
- typed WS message parsing
- connection state management

### Done when
- browser successfully subscribes to an online agent
- `subscribe_ack` and `stream_config` are handled correctly
- `AGENT_OFFLINE` and socket close are handled correctly

---

## Phase 6 — Waterfall canvas

### Goal
Render live spectrogram frames.

### Build
- `WaterfallCanvas` component
- base64 float32 LE decoder
- simple amplitude-to-color mapping
- config-driven buffer sizing
- row-by-row draw pipeline

### Rules
- no full React rerender per frame
- canvas drawing must be imperative

### Done when
- live frames visibly render
- reconfig changes bin count without corrupting drawing
- offline/disconnect stops drawing cleanly

---

## Phase 7 — Live page polish

### Goal
The single-agent page is usable for demos.

### Build
- connection badge
- clearer empty/offline/loading states
- show last heartbeat / session id / compact status info
- reconnect/reload button if needed

### Done when
- page clearly communicates current state
- demo flow works without developer explanation

---

## Phase 8 — Frontend tests

### Goal
Lock down critical behavior.

### Write tests for
- login success/failure
- protected route redirect
- agents list rendering
- token create/revoke UI behavior
- viewer hook message handling
- config-first frame gating
- waterfall decode helper

### Minimum test priorities
1. auth flow
2. WS subscribe + config-first behavior
3. float32 payload decode correctness

---

## Phase 9 — End-to-end demo verification

### Goal
Prove the full product path works.

### Verify manually
1. start backend
2. bootstrap user
3. log in via browser
4. create token
5. run agent against server
6. open live page
7. confirm waterfall updates in real time
8. disconnect agent and verify UI flips offline

### Done when
- one real end-to-end demo works without patching code during the demo

---

## Coding agent constraints

The coding agent must follow these rules:

1. Do not modify backend API contracts
2. Do not add backend endpoints unless blocked and explicitly justified
3. Do not introduce global client state unless there is a clear reason
4. Keep the UI small and practical
5. Use TypeScript types for all server messages
6. Do not use `any` for API or WS payloads
7. Keep the waterfall implementation simple before optimizing visuals
8. Favor readable code over clever abstractions

---

## Recommended order of execution

Implement in this exact order:

1. web project bootstrap
2. login flow
3. agents list page
4. token panel
5. viewer WebSocket hook
6. waterfall canvas
7. live page polish
8. tests
9. end-to-end manual verification

Do not start with canvas before auth/routing/API basics exist.
Do not spend time styling before live frames render.

---

## Definition of done for the web MVP

The web MVP is done when all of the following are true:

- user can log in
- user can see owned agents
- user can create token and copy raw token once
- user can revoke token
- user can open one agent live page
- browser subscribes via `/ws/viewer`
- browser receives config then frames
- spectrogram visibly updates live
- agent disconnect visibly transitions UI to offline/error state

That is the MVP.

Everything else is later.
