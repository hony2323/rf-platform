# Agent — MVP Status

**Date:** 2026-04-09  
**Protocol version:** 0.3 (frozen)

This document describes what is implemented in the agent, what is stubbed or hollow, and what is missing before the agent can be considered production-ready for MVP.

---

## Data flow

```
IQSource
  → [iq_queue: bytes]
  → IQProcessor (parse_iq → FFT)
  → [frame_queue: SpectrumFrame]
  → Session._send_loop (encode → bandwidth limiter → WebSocket)

TelemetryLoop runs concurrently: heartbeat + agent_status → WebSocket
AgentRunner owns task lifecycle and reconnect loop
```

---

## What exists

### `domain/`

Frozen dataclasses and enums. Nothing else imports into here.

| Type | Purpose |
|---|---|
| `SampleFormat` | uint8, int16, float32, float64 — with `.bytes_per_sample` |
| `IQDescriptor` | Format of one IQ buffer: format, endianness, layout, rate, freq, dc_remove, normalize |
| `RFConfig` | FFT parameters: center_freq, sample_rate, fft_size, window_fn, bin_count |
| `FFTSemantics` | kind=power, scale=log, unit=dBFS, numeric_type=float32, bin_order=low_to_high |
| `SpectrumFrame` | Output of FFT pipeline: `payload: bytes` (float32 LE), timestamp, bin_count |
| `HardwareInfo` | Vendor/model/serial — informational, sent in `connect` |
| `DropCounters` | local_throttle, queue_overflow, server_rejected, parse_errors |
| `PipelineLatencies` | p50/p99 for parse_iq, FFT, encode_send; avg queue depths |
| `AgentMetrics` | Full snapshot: cpu, throttled, tx_bytes_per_sec, queue_depth, drops, pipeline |
| `ConnectionState` | DISCONNECTED → CONNECTING → CONNECTED → CONFIGURED → STREAMING |
| `WireEncoding` | JSON_BASE64, BINARY_WS |

---

### `source/`

| Component | Status |
|---|---|
| `IQSource` Protocol | `start()`, `stop()`, `run(queue)`, `descriptor` |
| `SimulatorSource` | Pure tone generator; optional rate limiting via leaky bucket |
| `WavSource` | WAV file replay (PCM uint8/int16, IEEE float32/float64); optional rate limiting |
| `SigMFSource` | SigMF recording replay (ci16, cf32, cf64, cu8); **no rate limiting** |
| RTL-SDR / hardware | **Does not exist** — see gaps |

---

### `processing/`

| Component | What it does |
|---|---|
| `parse_iq` | Stateless `(IQDescriptor, bytes) → IQParseResult \| IQParseError`. Normalizes int16/uint8/float64 to float32 in [-1.0, 1.0]. DC removal. Returns INCOMPLETE_SAMPLE if buffer is not sample-aligned. |
| `FFTProcessor` | Hann window → FFT → fftshift → log-power (dBFS). Configured from `RFConfig`. |
| `IQProcessor` | Wires parse_iq and FFT. Holds byte remainder across chunks. Accumulates float32 samples until fft_size are ready. Timestamps are set at dequeue time (proxy for hardware capture time). Emits `SpectrumFrame` to output queue. |

---

### `protocol/`

| Component | What it does |
|---|---|
| `JsonBase64Codec` | Encodes all outbound messages (connect, stream_config, heartbeat, agent_status, spectrum_frame) and decodes all inbound messages (connect_ack, stream_config_ack, error, disconnect). |
| `encode_spectrum_frame_binary_ws` | Binary WS frame: `[uint16 header_len][JSON header][raw float32 payload]`. |
| Inbound types | `ConnectAck`, `StreamConfigAck`, `ServerError`, `Disconnect` |

All message fields match protocol v0.3. `HardwareInfo` is in the domain but **not wired into `encode_connect`** — the `hardware` block is always omitted from outbound `connect` messages.

---

### `session/`

**`Session`** implements the five-state machine and owns the streaming loop.

| State | Transition |
|---|---|
| DISCONNECTED | → CONNECTING on `run()` |
| CONNECTING | → CONNECTED after `connect_ack` |
| CONNECTED | → CONFIGURED after `stream_config_ack` |
| CONFIGURED | → STREAMING immediately |
| STREAMING | → DISCONNECTED on error or cancellation |

`_send_loop`: dequeues `SpectrumFrame`, encodes, applies `BandwidthLimiter`, sends.  
`_recv_loop`: handles `disconnect`, `error` (fatal/non-fatal), runtime `stream_config_ack`.  
`request_config_update()`: sends a new `stream_config` mid-session and waits for ack.

**`bandwidth.py`** — pluggable outbound rate limiting:

| Class | Strategy |
|---|---|
| `BandwidthLimiter` | Protocol — `should_send(n_bytes) -> bool` |
| `DecimateLimiter` | Interval-based dropper. Interval recomputed per frame. Not a pacer — no delays, no buffering. |
| `DropLimiter` | Token-bucket. Starts full (one-second burst allowed). Drops when bucket is empty. |
| `make_limiter` | Factory — returns `None` (unlimited), `DecimateLimiter`, or `DropLimiter`. |

---

### `transport/`

`WebSocketTransport` wraps the `websockets` library.

- Connects with `Authorization: Bearer <token>` header
- Extracts `X-Session-Id` from the HTTP 101 response
- `send(str | bytes)` — text for JSON messages, bytes for binary_ws frames
- `recv()` — text only; raises `TypeError` on binary inbound (binary_ws frames are handled before reaching the recv loop)
- No retry logic — that belongs to `AgentRunner`

---

### `telemetry/`

| Component | What it does |
|---|---|
| `MetricsCollector` | Accumulates gauges (cpu, throttled, tx_bytes, queue) and drop counters. `snapshot()` returns `AgentMetrics` and resets drop counters. |
| `PipelineTiming` | Rolling window (default 200 samples) of per-stage latencies and queue depths. `snapshot()` returns p50/p99/mean. |
| `TelemetryLoop` | Two concurrent loops: heartbeat every N seconds, agent_status every M seconds. Only emits when `session.state == STREAMING`. |

---

### `config/`

`AgentConfig` is composed from:

| Sub-config | Fields |
|---|---|
| `AgentIdentity` | node_id, agent_version |
| `ServerConfig` | url (`ws://` or `wss://`), token |
| `RFConfig` | *(see domain)* |
| `IQDescriptor` | *(see domain)* |
| `QueueConfig` | iq_queue_size, frame_queue_size |
| `TelemetryConfig` | heartbeat_interval_s, status_interval_s |
| `ReconnectConfig` | initial_delay_s, max_delay_s, backoff_factor, jitter |
| `BandwidthConfig` | max_bytes_per_sec, strategy (decimate \| drop) |

`load_config_dict(raw: dict) → AgentConfig`: validates and types a raw dict. Cross-checks `iq.sample_rate_hz == rf.sample_rate_hz` and `iq.center_freq_hz == rf.center_freq_hz`.

---

### `app/`

`AgentRunner` wires all components together.

- `run_once()`: builds components, launches four concurrent tasks (source, processor, session, telemetry). Cancels siblings when any task exits. Cleans up source and transport on every exit path.
- `run_forever()`: retries `run_once()` with exponential backoff + optional jitter. Stops on `BuildFailure` or `CancelledError`.
- All components are injected via `RunnerFactories` — every factory can be replaced for testing.

---

### Tests

| Suite | Coverage |
|---|---|
| Unit | domain, config loader, parse_iq, FFT pipeline, IQ processor, protocol codec, session state machine, transport, metrics, telemetry loop, runner, WAV source, SigMF source |
| Integration | Handshake flow, streaming flow, runtime recovery (reconnect, mid-session config update, fatal/non-fatal errors) |
| Missing | `session/bandwidth.py` — no unit tests |

---

## Gaps and hollow pieces

These are things that exist in skeleton or partial form, or are missing entirely, that need to be addressed before MVP.

### Missing: Real SDR hardware source

There is no `IQSource` implementation that talks to real SDR hardware (RTL-SDR, HackRF, USRP, etc.). The three existing sources (Simulator, WAV, SigMF) are for development and testing only. A hardware source is the reason the agent exists.

### Missing: Production CLI entrypoint

`run_demo.py` is a development script, not a production entrypoint. There is no `python -m agent` or installed console script. A production agent needs a way to be started from a config file or environment variables without modifying source.

### Missing: Config file loading

`load_config_dict` accepts a Python dict. There is no function that reads a file (YAML, TOML, JSON) and feeds it to the loader. There is also no environment variable override layer (e.g. `AGENT_SERVER_TOKEN`).

### Gap: `BandwidthConfig` not in `load_config_dict`

`BandwidthConfig` was added to `AgentConfig` but `load_config_dict` does not parse the `bandwidth` section from raw input. Any config loaded via `load_config_dict` will always use the defaults (unlimited).

### Hollow: Agent status metrics are mostly zeros

`MetricsCollector` has setters for `throttled`, `tx_bytes_per_sec`, `queue_depth`, and `queue_fill_pct` but nothing calls them (except `set_cpu_usage_pct` in `run_demo.py`). Every `agent_status` message sent by a production agent reports:

```json
"throttled": false,
"tx_bytes_per_sec": 0,
"queue_depth": 0,
"queue_fill_pct": 0
```

These fields exist in the protocol and are expected by the server.

### Hollow: `server_rejected` drop counter never incremented

`DropCounters.server_rejected` is defined and serialized in `agent_status`, but `Session._recv_loop` does not call `inc_server_rejected()` when it receives an `INVALID_FRAME` or `FRAME_TOO_LARGE` error from the server. The counter is permanently zero.

### Gap: `HardwareInfo` not sent in `connect`

The protocol spec includes an optional `hardware` block in `connect`. `HardwareInfo` exists in the domain. Neither `AgentIdentity` nor `AgentConfig` has a hardware field, and `encode_connect` does not accept or send one. The server never receives hardware information.

### Gap: `SigMFSource` has no rate limiting

`WavSource` and `SimulatorSource` both support `rate_limit_msps` for benchmarking at real hardware speed. `SigMFSource` does not. It always reads at full disk speed.

### Gap: No graceful shutdown for production use

`AgentRunner.run_forever()` loops indefinitely with no shutdown hook. A production agent needs SIGTERM/SIGINT handling to stop accepting new frames, drain in-flight data, send a final `agent_status`, and exit cleanly.

### Gap: Timestamp is a proxy, not a capture time

`IQProcessor.run()` timestamps frames at the moment a chunk is dequeued from `iq_queue`, not at the moment the hardware captured it. For file sources this is acceptable; for a live hardware source, hardware-provided capture timestamps should be propagated.

### Gap: `RATE_EXCEEDED` and `CONFIG_REJECTED` not specially handled

`Session._recv_loop` treats all non-fatal server errors as "continue". `RATE_EXCEEDED` should probably trigger backpressure or reduce send rate. `CONFIG_REJECTED` (non-OK `stream_config_ack`) is a protocol violation and should raise `SessionError`.

### Missing: Bandwidth limiter tests

`session/bandwidth.py` has no unit tests. `DecimateLimiter` and `DropLimiter` are not covered.

---

## Post-MVP (do not implement)

Per protocol v0.3 and CLAUDE.md — deferred:

- `binary_ws` and `msgpack` wire encodings *(note: binary_ws is partially implemented but marked post-MVP in the protocol doc)*
- `epoch_ms` replacing `timestamp_utc`
- `data.phase_rad`, `data.psd_db`
- uint8 FFT output quantization
- `stream_id` beyond `"default"` (multi-tuner)
- Planar IQ layout
- REST snapshot endpoint
