# Session Manager Implementation Plan (Agent)

## Goal
Implement a **single-run session state machine** that performs the protocol v0.3 handshake and streams frames correctly.

## Source of Truth
- Session responsibilities and states: see project state fileciteturn0file0
- Protocol handshake and rules: fileciteturn1file2
- Test plan baseline: fileciteturn1file0
- Monorepo contract ownership (shared protocol): fileciteturn1file3

---

## Hard Boundaries

### Session owns
- state transitions
- handshake order
- inbound validation
- session_id / config_version storage
- frame gating
- frame_index lifecycle

### Session does NOT own
- websocket implementation
- retry/backoff
- runtime orchestration
- telemetry timing
- codec correctness
- FFT correctness

---

## State Machine

States:
- DISCONNECTED
- CONNECTING
- CONNECTED
- CONFIGURED
- STREAMING

---

## Required Flow

1. connect transport
2. read session_id from header
3. send connect
4. receive connect_ack
5. validate session_id
6. send stream_config
7. receive stream_config_ack
8. store config_version
9. allow frame sending
10. increment frame_index per frame

---

## Rules

### Handshake
- strict order required
- mismatch session_id → fail
- mismatch stream_id → fail

### Frames
- MUST NOT send before config ack
- frame_index starts at 0
- increments monotonically
- resets on config update

### Errors
- fatal=true → stop session
- fatal=false → continue
- disconnect → stop session
- transport failure → stop session

---

## Fake Test Infrastructure

### FakeTransport
- connect()
- send()
- recv()
- close()
- session_id_from_header

### FakeCodec
- encode_* → simple JSON/dict
- decode → return typed objects

### Helpers
- make_session
- make_frame
- make_connect_ack
- make_stream_config_ack
- make_error
- make_disconnect

---

## Test Groups

### 1. Initial
- starts in DISCONNECTED
- moves to CONNECTING

### 2. Handshake
- sends connect first
- stores header session_id
- validates connect_ack
- sends stream_config
- validates stream_config_ack

### 3. Streaming
- blocks frames before configured
- sends frames after configured
- frame_index = 0 start
- increments correctly

### 4. Errors
- fatal stops
- nonfatal continues
- disconnect stops
- send/recv failure stops

### 5. Config update (optional)
- resets frame_index
- replaces config_version

---

## Implementation Order

1. fake transport + codec
2. handshake tests
3. minimal session logic
4. frame gating tests
5. streaming loop
6. error handling
7. config update

---

## Acceptance Criteria

- handshake order enforced
- mismatches rejected
- no pre-config frames
- correct frame_index lifecycle
- correct error handling
- clean session restart

---
