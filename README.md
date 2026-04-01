# RF Platform

Monorepo for live RF spectrum streaming. SDR agents on edge devices push FFT frames over WebSocket to a central server, which relays them to web clients.

## Status

The agent is the primary active component. The server and web frontend are scaffolded but not yet implemented.

| Component | State |
|---|---|
| `agent/` | Complete — source, processing, session, transport, telemetry, runner |
| `server/` | Scaffold only — modules defined, no implementation |
| `web/` | Scaffold only |
| `protocol/` | Wire contract v0.3 frozen for MVP |

## Repository layout

```
agent/        Python package — runs on edge device with SDR hardware
server/       Python package — central relay server (stub)
web/          Node.js frontend — spectrum viewer UI (stub)
protocol/     Documentation only — wire contract v0.3
docs/         Architecture and product docs
scripts/      Dev tooling: demo runner, fake server, recording utilities
recordings/   RF recordings for local dev (gitignored — never commit)
```

## Agent

The agent reads IQ samples from an SDR (or a file), computes FFT frames, and streams them to the server over WebSocket using the v0.3 wire protocol.

### Pipeline

```
IQSource → [iq_queue] → IQProcessor (parse + FFT) → [frame_queue] → Session → WebSocket
                                                                           ↑
                                                                    TelemetryLoop (heartbeat + agent_status)
```

All four stages run as concurrent asyncio tasks managed by `AgentRunner`. Queues are bounded; back-pressure and drop counting happen at queue boundaries.

### Modules

| Module | Responsibility |
|---|---|
| `app/runner.py` | `AgentRunner` — wires all components, lifecycle, restart with backoff |
| `source/` | `IQSource` protocol, `SigMFSource` for `.sigmf-meta` recordings |
| `processing/` | `parse_iq` (stateless IQ parser), `IQProcessor` (FFT pipeline) |
| `session/` | Handshake state machine, frame_index, config_version, drop counters |
| `transport/` | WebSocket connection, Bearer auth, session_id from response header |
| `telemetry/` | `MetricsCollector`, `TelemetryLoop` (heartbeat + agent_status sender) |
| `protocol/` | `JsonBase64Codec` — encodes/decodes all wire messages |
| `config/` | `AgentConfig` and YAML loader with validation boundary |
| `domain/` | Frozen dataclasses and enums — no I/O |

### Setup

```bash
cd agent

# Install with dev dependencies
pip install -e ".[dev]"

# Install with SDR hardware support
pip install -e ".[dev,sdr]"
```

### Tests

```bash
cd agent

# All tests
pytest

# Unit tests only (no network)
pytest -m "not integration"

# Type check
mypy src/agent

# Lint / format
ruff check src/
ruff format src/
```

Integration tests are marked `@pytest.mark.integration` and require a real network socket. They use `FakeAgentServer` from `scripts/fake_server.py`.

## Live demo

Runs the full agent stack against a local fake server and prints rolling JSON metrics every second.

```bash
cd agent

# Synthetic source — unlimited speed
python ../scripts/run_demo.py --fft-size 4096 --duration 10

# Synthetic source — throttled to RTL-SDR speed
python ../scripts/run_demo.py --fft-size 1024 --rate-limit-msps 2.4 --duration 10

# Real SigMF recording (loops automatically)
python ../scripts/run_demo.py \
  --sigmf ../recordings/<name>/<name>.sigmf-meta \
  --fft-size 4096 \
  --duration 20
```

Sample output (LTE recording, 30.72 MSPS, FFT 4096):

```json
{"time_s": 7.0, "frames_received": 20607, "fps": 3014.44,
 "realtime_fps_needed": 7500.0, "realtime_ratio": 0.402, "can_keep_up": false,
 "ws_mb_per_sec": 63.54, "iq_msps": 12.35, "iq_mb_s": 98.78,
 "agent": {"cpu_pct": 98.32, "throttled": false, "drops": {...}}}
```

Key metrics explained:

| Field | Meaning |
|---|---|
| `fps` | Spectrum frames delivered to the server per second |
| `realtime_fps_needed` | `sample_rate_hz / fft_size` — frames/s needed to keep up with live SDR |
| `realtime_ratio` | `fps / realtime_fps_needed` — 1.0 means real-time, <1.0 means falling behind |
| `can_keep_up` | Whether the pipeline is sustaining real-time throughput |
| `ws_mb_per_sec` | Actual WebSocket bytes received by the server |
| `iq_msps` | IQ mega-samples/sec implied by the achieved frame rate |
| `cpu_pct` | Agent process CPU usage (single core, measured via `process_time`) |

## Scripts

| Script | Purpose |
|---|---|
| `scripts/run_demo.py` | Live demo with rolling metrics |
| `scripts/fake_server.py` | Real TCP WebSocket server implementing the v0.3 handshake — used by integration tests and the demo |
| `scripts/reduce_sigmf_file_size.py` | Trim large SigMF recordings for CI fixtures |

## Wire protocol

The agent-server wire contract (v0.3) is documented in `protocol/agent_server_contract_v0_3.md`. It is **frozen for MVP** — post-MVP items (binary encoding, multi-stream, msgpack) are explicitly deferred.

Handshake order: HTTP Upgrade → `connect` → `connect_ack` → `stream_config` → `stream_config_ack` → spectrum frames.
