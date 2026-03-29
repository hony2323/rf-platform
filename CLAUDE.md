# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

RF Platform is a monorepo for live RF spectrum streaming. SDR agents on edge devices push FFT frames over WebSocket to a central server, which relays them to web clients. The wire protocol (v0.3) is **frozen for MVP** — do not implement post-MVP items listed in the protocol docs.

## Repository layout

```
agent/        Python package — runs on edge device with SDR hardware
server/       Python package — central relay server
web/          Node.js frontend — spectrum viewer UI
protocol/     Documentation only — the wire contract (not code)
docs/         Architecture and product docs
recordings/   Large RF recordings for local dev (gitignored — never commit)
```

Test fixtures (small, committed, CI-safe) live in `agent/src/agent/tests/fixtures/`.
Full-length recordings go in `recordings/` at the repo root and are gitignored.

## Commands

### Agent (`cd agent`)

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Install with SDR hardware support
pip install -e ".[dev,sdr]"

# Run all tests
pytest

# Run unit tests only (skip integration)
pytest -m "not integration"

# Run a single test file
pytest src/agent/tests/unit/processing/test_parse_iq.py

# Run a single test
pytest src/agent/tests/unit/processing/test_parse_iq.py::test_name

# Type check
mypy src/agent

# Lint / format
ruff check src/
ruff format src/
```

Ruff is configured to enforce `E` (pycodestyle errors, including E501 line length), `F` (Pyflakes), and `UP` (pyupgrade). Import sorting (`I`), naming conventions (`N`), and warnings (`W`) are intentionally disabled.

### Server (`cd server`)

```bash
pip install -e .
```

No test runner or lint config defined yet — mirror the agent setup when adding.

### Web (`cd web`)

No scripts defined yet beyond the scaffold.

## Architecture

### Data flow

```
IQSource → [iq_queue] → Processor (parse_iq → FFT) → [frame_queue] → Session → WebSocket → Server → Web clients
```

The agent's `AgentRuntime` runs all four stages as concurrent asyncio tasks. Queues are bounded; back-pressure and drop counting happen at the queue boundaries.

### Agent module responsibilities

| Module | Owns |
|---|---|
| `domain/` | Frozen dataclasses and enums only. No I/O. Everything else imports from here. |
| `source/base.py` | `IQSource` Protocol — hardware/file/simulator abstraction. Produces raw `bytes` blocks. |
| `processing/parse_iq.py` | Stateless `(IQDescriptor, bytes) → IQParseResult | IQParseError`. Implementation goes in `source/iq_parser.py`. |
| `app/` | `AgentRuntime` Protocol — wires components together, manages asyncio task lifecycle. |
| `config/` | `AgentConfig` — all tunables (queue sizes, server URL, node_id, etc.). |
| `session/` | Manages handshake state machine, frame_index, config_version, drop counters. |
| `transport/` | Outbound WebSocket connection, Bearer auth header. |
| `telemetry/` | Reads CPU/queue metrics, builds `AgentMetrics`. |
| `protocol/` | Serializes/deserializes wire messages (json_base64 MVP). |

### Server module responsibilities

| Module | Owns |
|---|---|
| `transport/` | WebSocket lifecycle, HTTP Upgrade, Bearer token validation, issues `session_id` via `X-Session-Id` header. Knows nothing about RF. |
| `sessions/` | Registry: `(session_id, stream_id, config_version) → stream_config`. Enforces handshake order; source of `NO_STREAM_CONFIG` errors. |
| `relay/` | Fanout: distributes spectrum frames to connected web clients. |
| `auth/` | Token validation. Stub only — implementation pending. |
| `protocol/` | Wire message parsing (mirrors agent). |
| `domain/` | Server-side domain types. |

### Protocol / identity model

The wire contract lives in `protocol/agent_server_contract_v0_3.md`. Key points:

- **Handshake order is strictly enforced**: HTTP Upgrade → `connect` → `stream_config` → frames. Frames before `stream_config` → `NO_STREAM_CONFIG`.
- **Identity**: `node_id` (stable install), `session_id` (server-issued per connection), `stream_id` (default `"default"` in MVP), `config_version` (monotonic, server-assigned per stream_config), `frame_index` (resets on config change — gaps mean dropped frames).
- **Wire encoding**: `json_base64` is the only MVP encoding. Payload is base64 float32 LE. Tests must assert on **decoded values**, never raw encoded bytes.
- **Errors**: `fatal: true` → server closes connection, agent must reconnect. `fatal: false` → agent should fix and retry. `AUTH_FAILED` is always an HTTP 401, never a WebSocket message.

### IQ parser contract

`parse_iq(descriptor, buffer)` is stateless. The caller is responsible for holding remainder bytes across chunk boundaries. The parser returns `INCOMPLETE_SAMPLE` if `len(buffer) % bytes_per_sample != 0`. Normalization and DC removal happen inside the parser; the FFT stage always receives float32 in `[-1.0, 1.0]`.

Normalization ops by format: `int16 → / 32768.0`, `uint8 → (x - 127.5) / 127.5`, `float64 → downcast after normalize`. DC removal: subtract `mean(I)` and `mean(Q)` after normalization.

The most important parser test is the **known-signal test** (pure tone at a known frequency — verify FFT peak lands in the expected bin). See `protocol/iq_input_schema.md` for the full test invariant list and the known-signal recipe.

## Key constraints

- Python ≥ 3.10 required; use `from __future__ import annotations` in all agent/server files.
- `asyncio_mode = "auto"` is set — all async tests run automatically.
- Mark integration tests with `@pytest.mark.integration`; they can be deselected with `-m "not integration"`.
- Do not add `config_version` to `stream_config` messages — the server assigns it.
- `bin_count` in `stream_config` is payload-authoritative for buffer allocation; `fft_size` is the authoritative RF parameter.
- Post-MVP items (planar layout, binary_ws, msgpack, multi-tuner stream_id, epoch_ms, phase_rad, psd_db) are explicitly deferred — do not implement them.
