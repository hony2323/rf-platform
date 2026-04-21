# Agent Project State

Current as of: 2026-03-29

---

## Overview

The agent is a Python package (`rf-agent`) that runs on an edge device with an SDR.
Its job: read raw IQ bytes from a hardware source, process them into log-power FFT frames,
and stream those frames over WebSocket to the server using the protocol v0.3 wire contract.

**Overall status**: the data pipeline (IQ source → parser → FFT → frame) is fully
implemented and tested. All surrounding infrastructure (session state machine, transport,
protocol codec, telemetry, runtime orchestration) exists as typed Protocol stubs only —
no concrete implementations yet.

---

## Module Inventory

| Module | File | LOC | Status | Role |
|--------|------|-----|--------|------|
| Domain | `domain/__init__.py` | 185 | **Implemented** | Enums + frozen dataclasses |
| Config | `config/__init__.py` | 60 | **Implemented** | Typed config composition |
| IQ Parser | `processing/parse_iq.py` | 136 | **Implemented** | `(descriptor, bytes) → float32[]` |
| FFT Pipeline | `processing/fft_pipeline.py` | 94 | **Implemented** | Windowed FFT → log-power dBFS |
| IQ Processor | `processing/processor.py` | 111 | **Implemented** | Parser + accumulation + FFT pipeline |
| SigMF Source | `source/sigmf.py` | 116 | **Implemented** | File-based IQ source |
| IQ Source | `source/base.py` | 38 | Protocol stub | Hardware/file/simulator abstraction |
| Session | `session/__init__.py` | 75 | Protocol stub | 5-state machine + frame dispatch |
| Transport | `transport/__init__.py` | 64 | Protocol stub | WebSocket + Bearer auth |
| Protocol Codec | `protocol/__init__.py` | 134 | Protocol stub | Wire message encode/decode |
| Telemetry | `telemetry/__init__.py` | 45 | Protocol stub | Heartbeat + agent_status |
| App Runtime | `app/__init__.py` | 43 | Protocol stub | Task orchestration |

---

## Implemented Modules — Detail

### Domain (`domain/__init__.py`)

Frozen dataclasses and enums. No I/O. Everything else imports from here.

**Enums**

| Enum | Values |
|------|--------|
| `SampleFormat` | `FLOAT32`, `INT16`, `FLOAT64`, `UINT8` |
| `Endianness` | `LITTLE`, `BIG` |
| `Layout` | `INTERLEAVED` (planar is post-MVP) |
| `BinOrder` | `LOW_TO_HIGH`, `NATURAL` |
| `WindowFunction` | `HANN` |
| `ConnectionState` | `DISCONNECTED`, `CONNECTING`, `CONNECTED`, `CONFIGURED`, `STREAMING` |
| `WireEncoding` | `JSON_BASE64` |

`SampleFormat.bytes_per_sample`: FLOAT32=8, INT16=4, FLOAT64=16, UINT8=2.

**Dataclasses**

| Class | Key fields | Notes |
|-------|-----------|-------|
| `IQDescriptor` | `sample_format`, `endianness`, `layout`, `sample_rate_hz`, `center_freq_hz`, `dc_offset_remove=True`, `normalize=True` | Matches `iq_input_schema.md` exactly |
| `RFConfig` | `center_freq_hz`, `sample_rate_hz`, `fft_size`, `window_fn=HANN`, `bin_count=None` | `effective_bin_count` property: returns `bin_count` if set, else `fft_size` |
| `FFTSemantics` | `kind="power"`, `scale="log"`, `unit="dBFS"`, `numeric_type="float32"`, `bin_order=LOW_TO_HIGH` | MVP-frozen constants |
| `SpectrumFrame` | `payload: bytes`, `timestamp_utc: str`, `bin_count: int` | Output of pipeline, input to session |
| `HardwareInfo` | `vendor`, `model`, `serial` (all optional) | Sent in `connect` message |
| `DropCounters` | `local_throttle=0`, `queue_overflow=0`, `server_rejected=0` | Telemetry |
| `AgentMetrics` | `cpu_usage_pct`, `throttled`, `tx_bytes_per_sec`, `queue_depth`, `queue_fill_pct`, `drops` | Sent in `agent_status` |

---

### Config (`config/__init__.py`)

All frozen dataclasses. No loading from file/env yet — manual construction only.

| Class | Required | Defaults | Notes |
|-------|----------|----------|-------|
| `AgentIdentity` | `node_id` | `agent_version="0.3.0"` | |
| `ServerConfig` | `url`, `token` | — | `wss://…` + bearer token |
| `QueueConfig` | — | `iq_queue_size=4`, `frame_queue_size=8` | Bounded queues |
| `TelemetryConfig` | — | `heartbeat_interval_s=5.0`, `status_interval_s=10.0` | |
| `ReconnectConfig` | — | `initial_delay_s=1.0`, `max_delay_s=30.0`, `backoff_factor=2.0`, `jitter=True` | Exponential backoff |
| `AgentConfig` | `identity`, `server`, `rf`, `iq` | `stream_id="default"`, `wire_encoding=JSON_BASE64`, sub-configs via `default_factory` | Root composition |

---

### IQ Parser (`processing/parse_iq.py`)

Stateless `parse_iq(descriptor, buffer) → IQParseResult | IQParseError`.

**Error codes**

| Code | Trigger |
|------|---------|
| `EMPTY_BUFFER` | `len(buffer) == 0` |
| `INCOMPLETE_SAMPLE` | `len(buffer) % bytes_per_sample != 0` |
| `UNSUPPORTED_FORMAT` | `_decode_samples` raises `_UnhandledFormatError` (defensive) |
| `UNSUPPORTED_LAYOUT` | `layout != INTERLEAVED` |

**Normalization per format**

| Format | `normalize=True` | `normalize=False` |
|--------|-----------------|-------------------|
| FLOAT32 | clamp to `[-1.0, 1.0]` | clamp to `[-1.0, 1.0]` (always clamped) |
| INT16 | `raw / 32768.0` | raw int16 as float32 |
| UINT8 | `(raw - 127.5) / 127.5` | raw uint8 as float32 |
| FLOAT64 | downcast to float32 | downcast to float32 |

DC removal (when `dc_offset_remove=True`): subtracts `mean(I)` and `mean(Q)` after normalization.

---

### FFT Pipeline (`processing/fft_pipeline.py`)

`FFTProcessor` — call `configure(rf_config)` then `process(samples, timestamp_utc) → SpectrumFrame`.

Pipeline steps: split I/Q → upcast to float64 → form complex → apply Hann window →
FFT → normalize by coherent window gain → power → fftshift → 10·log10 (floor −120 dB) →
cast to float32 → pack as LE bytes.

Output `SpectrumFrame.bin_count = rf_config.effective_bin_count`. Payload is sliced to
`bin_count` bins, enabling future frequency cropping without changing the FFT size.

---

### IQ Processor (`processing/processor.py`)

`IQProcessor(descriptor, rf_config)` — wires parse_iq + sample accumulation + FFTProcessor.

**Public API**

| Method | Description |
|--------|-------------|
| `configure(rf_config)` | Hot-swap RF config. Flushes sample buffer and byte remainder. Does **not** reset `parse_error_count`. |
| `push(chunk, timestamp_utc) → list[SpectrumFrame]` | Feed one raw byte chunk. Returns 0..N frames. |
| `async run(iq_queue, frame_queue)` | Drain queue, push frames. Runs until cancelled. |
| `parse_error_count: int` | Lifetime counter of `parse_iq` errors. Observable for debugging. |

**Byte remainder handling**: trailing bytes that form an incomplete sample are held and
prepended to the next `push()` call. `configure()` clears the remainder.

**Sample accumulation**: samples from successive `push()` calls are buffered until
`fft_size` complex samples are ready, then flushed to FFTProcessor. Leftover samples
carry forward.

---

### SigMF Source (`source/sigmf.py`)

`SigMFSource(meta_path, block_size=65536)` — reads a `.sigmf-meta`/`.sigmf-data` pair.

Supported datatypes: `ci16_le`, `ci16_be`, `cf32_le`, `cf32_be`, `cf64_le`, `cf64_be`,
`cu8_le`, `cu8_be`. Raises `UnsupportedSigMFDatatypeError` on anything else.

`run(output_queue)` reads the data file in aligned blocks (trimmed to `bytes_per_sample`
boundary) and pushes `bytes` to the queue. Blocking file I/O — acceptable for MVP/dev.

---

## Protocol Stubs — What They Define

These modules contain only `Protocol` classes (structural interfaces). No runnable code.

| Module | Defines |
|--------|---------|
| `source/base.py` | `IQSource` — `start()`, `stop()`, `run(output_queue)`, `descriptor` property |
| `session/__init__.py` | `Session` — 5-state machine, `run(frame_queue)`, `request_config_update()` |
| `transport/__init__.py` | `Transport` — `connect()`, `send()`, `recv()`, `close()`, `session_id_from_header` |
| `protocol/__init__.py` | `ProtocolCodec` — `encode_connect`, `encode_stream_config`, `encode_spectrum_frame`, `encode_heartbeat`, `encode_agent_status`, `decode`. Also defines inbound message dataclasses: `ConnectAck`, `StreamConfigAck`, `Disconnect`, `ServerError`. |
| `telemetry/__init__.py` | `MetricsCollector`, `Telemetry` |
| `app/__init__.py` | `AgentRuntime` — `run()`, `shutdown()` |
| `processing/__init__.py` | `Processor` Protocol — `push()`, `configure()`, `run()` |

---

## Test Suite

**Total: 90 unit tests. 0 integration tests (placeholder markers exist).**

All tests pass. All run in < 1 second.

---

### `tests/unit/config/test_agent_config.py` — 25 tests

Tests all config dataclasses and `RFConfig` computed properties.

| Test | What it verifies |
|------|-----------------|
| `test_agent_identity_stores_node_id` | `node_id` field stored correctly |
| `test_agent_identity_default_version` | `agent_version` defaults to `"0.3.0"` |
| `test_agent_identity_is_frozen` | `FrozenInstanceError` on mutation |
| `test_server_config_stores_url_and_token` | Both fields stored |
| `test_server_config_is_frozen` | Immutable |
| `test_queue_config_defaults` | `iq_queue_size=4`, `frame_queue_size=8` |
| `test_queue_config_accepts_custom_sizes` | Override works |
| `test_telemetry_config_defaults` | `heartbeat=5.0s`, `status=10.0s` |
| `test_reconnect_config_defaults` | `initial=1s`, `max=30s`, `factor=2.0`, `jitter=True` |
| `test_rf_config_bin_size_hz` | `sample_rate_hz / fft_size` |
| `test_rf_config_baseband_edges_are_symmetric` | `±sample_rate/2` |
| `test_rf_config_baseband_span_equals_sample_rate` | `end - start == sample_rate` |
| `test_rf_config_default_window_is_hann` | Default `WindowFunction.HANN` |
| `test_rf_config_is_frozen` | Immutable |
| `test_agent_config_default_wire_encoding` | `WireEncoding.JSON_BASE64` |
| `test_agent_config_uses_default_sub_configs` | All three sub-configs instantiated |
| `test_agent_config_default_sub_configs_are_independent_instances` | `default_factory` creates separate objects |
| `test_agent_config_accepts_custom_sub_configs` | Override works |
| `test_agent_config_is_frozen` | Immutable |
| `test_rf_config_default_bin_count_is_none` | `bin_count` field defaults to `None` |
| `test_rf_config_effective_bin_count_defaults_to_fft_size` | `None` → returns `fft_size` |
| `test_rf_config_effective_bin_count_uses_explicit_value` | Explicit `bin_count=512` returned |
| `test_rf_config_effective_bin_count_does_not_equal_fft_size_when_set` | `512 != 1024` |
| `test_agent_config_default_stream_id` | `stream_id` defaults to `"default"` |
| `test_agent_config_accepts_custom_stream_id` | Override works |

---

### `tests/unit/processing/test_iq_parser.py` — 22 tests

Covers all parser code paths: formats, normalization, DC removal, error codes, byte order,
and a real SigMF fixture.

| Test | What it verifies |
|------|-----------------|
| `test_parse_float32_interleaved_known_signal_peak_bin_matches_expected` | **Anchor test.** 100 kHz tone → parse → FFT → peak bin at correct frequency |
| `test_parse_float32_roundtrip_values_preserved` | Float32 bytes survive parse unchanged |
| `test_parse_int16_normalizes_using_divide_by_32768` | INT16 normalize: `raw / 32768.0` |
| `test_parse_uint8_normalizes_using_center_and_scale` | UINT8 normalize: `(x - 127.5) / 127.5`, range `[-1, 1]` |
| `test_parse_applies_dc_offset_removal_when_enabled` | I/Q channel means ≈ 0 after removal |
| `test_parse_rejects_incomplete_sample` | `INCOMPLETE_SAMPLE` for buffer not aligned to `bytes_per_sample` |
| `test_parse_real_ci16_succeeds` | Real LTE fixture (ci16_le, 64k samples) parses without error |
| `test_parse_real_ci16_sample_count_matches_file_size` | `sample_count == file_bytes / 4` |
| `test_parse_real_ci16_output_length_is_sample_count_times_two` | `len(samples) == sample_count * 2` |
| `test_parse_real_ci16_output_dtype_is_float32` | `dtype == float32` |
| `test_parse_real_ci16_normalized_values_within_unit_range` | All values in `[-1.0, 1.0]` |
| `test_parse_real_ci16_signal_has_nonzero_energy` | `std(samples) > 0.01` — guards against silent zero-fill |
| `test_parse_real_ci16_dc_removal_reduces_channel_means` | `|mean(I)| < 1e-4`, `|mean(Q)| < 1e-4` |
| `test_parse_empty_buffer_returns_empty_buffer_error` | Direct `EMPTY_BUFFER` error code test |
| `test_parse_float32_clamps_values_exceeding_unit_range` | `2.0 → 1.0`, `-3.0 → -1.0` |
| `test_parse_float32_values_within_range_are_unchanged` | Clamp does not alter valid values |
| `test_parse_int16_normalize_false_returns_raw_integer_scale` | `normalize=False` skips division |
| `test_parse_uint8_normalize_false_returns_raw_byte_values` | `normalize=False` skips centering |
| `test_parse_big_endian_int16_normalizes_correctly` | BE int16 bytes decoded and normalized |
| `test_parse_big_endian_int16_differs_from_little_endian` | Same bytes, different endianness → different values |
| `test_parse_float64_output_dtype_is_float32` | FLOAT64 input downcasts to float32 |
| `test_parse_float64_values_match_float32_downcast` | Values survive downcast within float32 precision |
| `test_parse_returns_unsupported_format_error_when_decode_raises` | `_UnhandledFormatError` → `UNSUPPORTED_FORMAT` (via monkeypatch) |

---

### `tests/unit/processing/test_fft_pipeline.py` — 8 tests

Tests `FFTProcessor` in isolation.

| Test | What it verifies |
|------|-----------------|
| `test_fft_processor_requires_configure_before_process` | `RuntimeError` before `configure()` |
| `test_fft_processor_output_payload_length_equals_bin_count_times_four` | `len(payload) == bin_count * 4` |
| `test_fft_processor_applies_hann_window_when_configured` | Output matches manual reference computation step-by-step |
| `test_fft_processor_produces_log_power_float32_payload` | dtype=float32, all finite, at least one value < 0 dBFS |
| `test_fft_processor_outputs_bins_in_low_to_high_order` | Tone at +1 kHz peaks at `fft_size//2 + k`, not `fft_size//2 - k` |
| `test_fft_processor_timestamp_is_capture_start_not_processing_end` | `timestamp_utc` passed through verbatim |
| `test_fft_processor_reconfigure_changes_output_shape_and_resets_internal_state` | fft_size 512 → 1024 takes effect immediately |
| `test_fft_processor_rejects_wrong_sample_count` | `ValueError` for mismatched sample count |

---

### `tests/unit/processing/test_processor.py` — 23 tests

Tests `IQProcessor` — byte handling, accumulation, reconfiguration, output properties,
async run, and error counting.

**Single-chunk push**

| Test | What it verifies |
|------|-----------------|
| `test_push_exactly_fft_size_samples_emits_one_frame` | Happy path |
| `test_push_fewer_than_fft_size_samples_emits_no_frame` | Partial chunk buffered |
| `test_push_two_fft_size_samples_emits_two_frames` | Two frames from one large chunk |
| `test_push_empty_bytes_emits_no_frame` | Empty chunk returns `[]` |

**Cross-chunk accumulation**

| Test | What it verifies |
|------|-----------------|
| `test_push_accumulates_two_half_chunks_into_one_frame` | First half: 0 frames; second half: 1 frame |
| `test_push_accumulated_samples_cleared_after_frame_emitted` | Buffer resets after emission |
| `test_push_leftover_samples_carry_forward_to_next_frame` | 1.5× chunk then 0.5× chunk: 1 frame each |

**Byte remainder handling**

| Test | What it verifies |
|------|-----------------|
| `test_push_holds_partial_sample_bytes_as_remainder` | Trailing 7 bytes held in `_remainder` |
| `test_push_remainder_prepended_to_next_chunk` | Remainder + next chunk = complete sample |
| `test_push_all_incomplete_bytes_held_when_chunk_too_small` | Chunk < `bytes_per_sample` fully held |

**Output frame properties**

| Test | What it verifies |
|------|-----------------|
| `test_push_frame_bin_count_equals_fft_size` | `bin_count == fft_size` |
| `test_push_frame_payload_length_is_bin_count_times_four` | `len(payload) == bin_count * 4` |
| `test_push_frame_payload_is_finite_float32` | dtype=float32, all values finite |
| `test_push_frame_timestamp_passed_through` | Passed through verbatim |

**Reconfiguration**

| Test | What it verifies |
|------|-----------------|
| `test_configure_changes_fft_size` | New `fft_size` takes effect on next push |
| `test_configure_flushes_accumulated_samples` | Partial buffer discarded on reconfigure |
| `test_configure_clears_byte_remainder` | `_remainder` reset to `b""` |

**Async run**

| Test | What it verifies |
|------|-----------------|
| `test_run_emits_frame_to_output_queue` | Frame reaches `frame_queue` after one queue item |
| `test_run_accumulates_across_multiple_queue_items` | Two half-chunks across two queue gets → 1 frame |

**Error counting**

| Test | What it verifies |
|------|-----------------|
| `test_push_parse_error_increments_error_count` | `parse_error_count` increments per error; accumulates |
| `test_parse_error_count_not_reset_by_configure` | Counter preserved across `configure()` |

---

### `tests/unit/source/test_sigmf_source.py` — 13 tests

Tests `SigMFSource` against the trimmed LTE uplink SigMF fixture (ci16_le, 847 MHz,
30.72 Msps, 256 KB / 64k complex samples).

**Descriptor tests** (meta file only, no data file read)

| Test | What it verifies |
|------|-----------------|
| `test_sigmf_source_descriptor_sample_format_matches_ci16_le` | `sample_format == INT16` |
| `test_sigmf_source_descriptor_endianness_is_little` | `endianness == LITTLE` |
| `test_sigmf_source_descriptor_layout_is_interleaved` | `layout == INTERLEAVED` |
| `test_sigmf_source_descriptor_sample_rate_matches_meta` | `sample_rate_hz == 30_720_000` |
| `test_sigmf_source_descriptor_center_freq_matches_capture` | `center_freq_hz == 847_000_000` |
| `test_sigmf_source_descriptor_raises_before_start` | `RuntimeError` before `start()` |
| `test_sigmf_source_start_raises_on_unsupported_datatype` | `UnsupportedSigMFDatatypeError` for unknown datatype |

**run() tests** (reads data file, cancels after a few blocks)

| Test | What it verifies |
|------|-----------------|
| `test_sigmf_source_run_produces_bytes` | Blocks are non-empty `bytes` |
| `test_sigmf_source_run_blocks_are_aligned_to_bytes_per_sample` | `len(block) % bps == 0` |
| `test_sigmf_source_run_block_size_respected_approximately` | `len(block) <= block_size` |
| `test_sigmf_source_run_blocks_parse_without_error` | Blocks pass through `parse_iq` cleanly |
| `test_sigmf_source_run_parsed_samples_are_float32` | `dtype == float32` |
| `test_sigmf_source_run_parsed_samples_normalized_to_unit_range` | All values in `[-1.0, 1.0]` |

---

## Test Fixtures

Defined in `tests/conftest.py`.

| Fixture | Type | Purpose |
|---------|------|---------|
| `sigmf_lte_meta_path` | `Path` | Path to the trimmed LTE SigMF fixture `.sigmf-meta` |
| `sigmf_lte_buffer` | async `SigMFBuffer` | Descriptor + raw bytes via `SigMFSource` — for source-layer tests |
| `lte_ci16_raw` | `SigMFBuffer` | Descriptor + raw bytes with hardcoded descriptor — for parser tests, independent of `SigMFSource` |

Fixture separation: parser tests use `lte_ci16_raw` (no `SigMFSource` dependency); source
tests use `sigmf_lte_meta_path`.

---

## What Is Not Yet Implemented

The following must be implemented before the agent can run end-to-end:

| Component | What's needed |
|-----------|---------------|
| `transport/` | Concrete `websockets`-backed `Transport` implementation with Bearer auth |
| `protocol/` | `json_base64` codec: encode outbound messages, decode inbound messages |
| `session/` | 5-state machine, handshake, frame dispatch, backoff/reconnect loop |
| `telemetry/` | Timer-driven heartbeat and `agent_status` sender |
| `app/` | `AgentRuntime` — wires all components, manages asyncio task lifecycle |
| Config loading | `from_file()` / `from_env()` / CLI parser for `AgentConfig` |
| Hardware source | RTL-SDR or similar concrete `IQSource` implementation (requires `.[sdr]` extra) |

---

## Known Remaining Issues

These are acceptable for MVP but should be addressed before production:

| Issue | Severity | Location |
|-------|----------|----------|
| Blocking file I/O in `SigMFSource.run()` | Low | `source/sigmf.py` — use `asyncio.to_thread` for production SDR |
| Timestamp is dequeue time, not capture time | Low | `processor.py` — MVP trade-off; degrades under backpressure |
| No config validation | Low | `config/__init__.py` — `fft_size > 0`, queue sizes, etc. unchecked |
| No config loading | Low | `config/__init__.py` — manual construction only |
| `stop()` cannot interrupt a running source | Low | `source/sigmf.py` — cancellation must come via task cancel |
| `bin_count != fft_size` not yet sliced in FFT output | Low | `fft_pipeline.py` correctly slices, but no post-MVP cropping logic |
