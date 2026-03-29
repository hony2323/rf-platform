# Audit Report: Implementation vs. Specification

## Overall Status

The project is in **early-mid development**. The data pipeline (IQ -> FFT) is solid and spec-compliant. Everything from session outward is protocol-only (interfaces with no implementation). The server is entirely empty scaffolding.

---

## What's Implemented and Correct

### 1. Domain types — Fully match the protocol spec

- All enums (`SampleFormat`, `Endianness`, `Layout`, `BinOrder`, `WindowFunction`, `ConnectionState`, `WireEncoding`) align with `agent_server_contract_v0_3.md`
- `IQDescriptor` matches `iq_input_schema.md` exactly (fields, defaults, derived `bytes_per_sample`)
- `RFConfig` correctly derives `bin_size_hz`, `baseband_start_hz`, `baseband_end_hz`
- `FFTSemantics` has correct MVP defaults (`power`, `log`, `dBFS`, `float32`, `LOW_TO_HIGH`)
- `SpectrumFrame`, `HardwareInfo`, `DropCounters`, `AgentMetrics` all match the wire spec
- All dataclasses are frozen as required

### 2. IQ Parser (`parse_iq`) — Fully compliant with `iq_input_schema.md`

- Stateless, source-agnostic
- All 4 formats implemented with correct normalization: `int16 -> /32768.0`, `uint8 -> (x-127.5)/127.5`, `float64 -> downcast`, `float32 -> direct`
- DC removal correctly subtracts per-channel mean after normalization
- All 5 error codes implemented (`EMPTY_BUFFER`, `INCOMPLETE_SAMPLE`, `UNSUPPORTED_FORMAT`, `UNSUPPORTED_LAYOUT`, `INVALID_DESCRIPTOR`)
- Output invariants satisfied: `len(samples) == sample_count * 2`, float32 output

### 3. FFT Pipeline — Correct per spec

- Hann window, float64 internal precision, coherent window gain normalization
- `fftshift` for `LOW_TO_HIGH` bin order
- Log-power dBFS output with -120 dB floor
- Float32 LE output, `bin_count = fft_size` (correct for MVP)

### 4. IQProcessor — Correctly chains parse_iq -> accumulation -> FFT

- Handles byte remainders across chunk boundaries (spec requirement)
- Sample accumulation until `fft_size` samples ready
- `configure()` flushes all state (sample buffer, remainder)
- `async run()` integrates with asyncio queues

### 5. SigMFSource — Working file-based IQ source

- Parses SigMF metadata correctly
- Block-aligned reads

### 6. Config — Comprehensive, covers all tunables

- `AgentConfig` composes identity, server, RF, IQ, wire encoding, queues, telemetry, reconnect

### 7. Tests — Good coverage of implemented modules (~60+ tests across 6 files)

---

## Protocol Interfaces (Correct Shape, No Implementation)

These are well-defined and match the spec, but have **zero implementation code**:

| Module | Status | Notes |
|--------|--------|-------|
| `protocol/` (ProtocolCodec) | Interface only | Signatures match all 8 wire message types. Missing `encode_disconnect` but agent doesn't send disconnect, so correct. |
| `session/` (Session) | Interface only | 5-state machine documented, `run()` and `request_config_update()` specified. |
| `transport/` (Transport) | Interface only | `session_id_from_header` for `X-Session-Id` capture is there. |
| `telemetry/` (MetricsCollector, Telemetry) | Interface only | `reset_drops()` semantics match "counts since last agent_status". |
| `app/` (AgentRuntime) | Interface only | Wiring diagram matches the data flow spec. |

---

## Issues and Gaps Found

### 1. `RFConfig` is missing `bin_count`

The protocol says `bin_count` is "payload-authoritative for buffer allocation" and may differ from `fft_size`. Currently `RFConfig` has no `bin_count` field; the FFT pipeline hardcodes `bin_count = fft_size`. This is acceptable for MVP (where they're equal), but the domain model doesn't capture this distinction. The `stream_config` message spec includes `bin_count` as a separate field — when the codec is implemented, it will need this value.

### 2. `stream_config` is missing `timestamp_utc`

The protocol requires `timestamp_utc` in `stream_config` messages. The `ProtocolCodec.encode_stream_config()` signature doesn't include a timestamp parameter. It takes `(node_id, session_id, stream_id, rf_config, fft_semantics)` but no timestamp.

### 3. `heartbeat` is missing `timestamp_utc`

Same issue. `encode_heartbeat(node_id, session_id)` has no timestamp parameter, but the wire format requires it.

### 4. `agent_status` is missing `timestamp_utc`

`encode_agent_status(node_id, session_id, metrics)` has no timestamp. The wire spec requires it.

### 5. `spectrum_frame` timestamp

`encode_spectrum_frame()` takes a `SpectrumFrame` which has `timestamp_utc`, but this should be verified when implementing to ensure the frame's timestamp (capture time) is used, not processing time.

### 6. `connect` message is missing `protocol_version` in codec signature

The `encode_connect()` takes `(node_id, agent_version, requested_encoding, hardware)` but the wire spec requires `protocol_version: "0.3"`. This could be hardcoded in implementation, but it's not in the signature.

### 7. No `stream_id` handling in config

`AgentConfig` doesn't have a `stream_id` field. The protocol uses `"default"` for MVP, but there's no place to configure or hold this value. The session will need to source it from somewhere.

### 8. Server is entirely empty

All 14 Python files are `__init__.py` with zero code. No transport, no session registry, no relay, no auth, no protocol codec. The server cannot accept connections, perform handshakes, or relay frames.

### 9. `float32` normalization has no clamp check

The `iq_input_schema.md` says `float32` normalization is "clamp check only", implying values should be clamped to `[-1.0, 1.0]`. Current code does a direct copy with no clamp. If hardware produces out-of-range values, they pass through unchecked.

### 10. `INVALID_DESCRIPTOR` error code is dead code

The parser has the `INVALID_DESCRIPTOR` error code but there's no path in `parse_iq()` that returns it. It's defined but unreachable.

---

## Test Coverage vs. Test Plan (`agent_next_tests.md`)

The test plan specifies ~60 tests across 8 sections. Current status:

| Section | Specified | Implemented | Status |
|---------|-----------|-------------|--------|
| IQ Parser | 11 tests | ~26 tests | **Exceeds plan** |
| Protocol Codec | 13 tests | 0 | **Not started** (no codec impl) |
| Session Manager | 18 tests | 0 | **Not started** (no session impl) |
| FFT Pipeline | 8 tests | 7+ tests | **Mostly covered** |
| Telemetry | 5 tests | 0 | **Not started** |
| Config Validation | 4 tests | 17 tests | **Exceeds plan** |
| Transport | 5 tests | 0 | **Not started** |
| Integration | 5 tests | 0 | **Not started** |

---

## Summary

### What works well

- The data pipeline (IQ parsing -> FFT -> SpectrumFrame) is solid, spec-compliant, and well-tested
- Domain types are clean, frozen, and match the wire protocol
- Protocol interfaces are well-shaped for future implementation
- The architecture follows the documented data flow correctly

### What needs attention before moving forward

1. Add `timestamp_utc` parameter to `encode_stream_config`, `encode_heartbeat`, `encode_agent_status` codec signatures
2. Decide where `stream_id` and `protocol_version` are sourced (config vs hardcoded)
3. Consider adding `bin_count` to `RFConfig` even if `== fft_size` for MVP, to match the wire schema
4. The `INVALID_DESCRIPTOR` error code is unreachable — either add validation or remove the code
5. The entire networking stack (codec, transport, session, telemetry) needs implementation
6. Server is a blank slate
