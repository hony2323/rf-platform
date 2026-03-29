# Agent Implementation Review

Scope: only modules with concrete implementation code. Protocols, stubs, and the server are excluded.

---

## Implemented Modules

| Module | File | LOC | Role |
|--------|------|-----|------|
| Domain | `domain/__init__.py` | 177 | Enums + frozen dataclasses |
| Config | `config/__init__.py` | 58 | Typed config composition |
| IQ Parser | `processing/parse_iq.py` | 121 | Stateless `(descriptor, bytes) -> float32[]` |
| FFT Pipeline | `processing/fft_pipeline.py` | 94 | Windowed FFT -> log-power dBFS |
| IQ Processor | `processing/processor.py` | 109 | parse_iq + accumulation + FFT pipeline |
| SigMF Source | `source/sigmf.py` | 117 | File-based IQ source from SigMF recordings |

Test modules: 6 files, ~90 tests total.

---

## 1. Domain (`domain/__init__.py`)

### Enums

| Enum | Values | Spec reference |
|------|--------|----------------|
| `SampleFormat` | FLOAT32, INT16, FLOAT64, UINT8 | `iq_input_schema.md` sample_format field |
| `Endianness` | LITTLE, BIG | `iq_input_schema.md` endianness field |
| `Layout` | INTERLEAVED | MVP-only; planar is post-MVP |
| `BinOrder` | LOW_TO_HIGH, NATURAL | `agent_server_contract_v0_3.md` bin_order |
| `WindowFunction` | HANN | Only MVP window |
| `ConnectionState` | DISCONNECTED, CONNECTING, CONNECTED, CONFIGURED, STREAMING | 5-state session machine |
| `WireEncoding` | JSON_BASE64 | MVP-only encoding |

All enum values match the protocol spec string values exactly.

### `SampleFormat.bytes_per_sample`

| Format | Value | Spec |
|--------|-------|------|
| FLOAT32 | 8 | 2 x 4 bytes (I + Q) |
| INT16 | 4 | 2 x 2 bytes |
| FLOAT64 | 16 | 2 x 8 bytes |
| UINT8 | 2 | 2 x 1 byte |

All correct per `iq_input_schema.md`.

### Dataclasses

| Class | Frozen | Fields match spec | Notes |
|-------|--------|-------------------|-------|
| `IQDescriptor` | Yes | Yes | `dc_offset_remove=True`, `normalize=True` defaults match schema |
| `RFConfig` | Yes | Mostly | Missing `bin_count` (see issues) |
| `FFTSemantics` | Yes | Yes | MVP defaults: power/log/dBFS/float32/LOW_TO_HIGH |
| `SpectrumFrame` | Yes | Yes | payload bytes + timestamp_utc + bin_count |
| `HardwareInfo` | Yes | Yes | All fields optional, informational |
| `DropCounters` | Yes | Yes | 3 counters, all default 0 |
| `AgentMetrics` | Yes | Yes | Matches `agent_status` wire message fields |

### Derived properties on `RFConfig`

| Property | Formula | Matches spec |
|----------|---------|--------------|
| `bin_size_hz` | `sample_rate_hz / fft_size` | Yes |
| `baseband_start_hz` | `-(sample_rate_hz / 2)` | Yes |
| `baseband_end_hz` | `sample_rate_hz / 2` | Yes |

### Issues

- **`RFConfig` has no `bin_count` field.** The protocol spec says `bin_count` is "payload-authoritative for buffer allocation" and may differ from `fft_size`. For MVP they are equal, but the wire `stream_config` message includes `bin_count` as a distinct field. When the codec is implemented it will need to serialize this value. Either add `bin_count` to `RFConfig` or derive it in the codec.
- **`FFTSemantics` fields `kind`, `scale`, `unit`, `numeric_type` are plain `str`.** These could be enums for type safety, but since they are MVP-frozen constants this is a minor style point.

---

## 2. Config (`config/__init__.py`)

### Sub-configs

| Class | Required fields | Defaults | Notes |
|-------|----------------|----------|-------|
| `AgentIdentity` | `node_id` | `agent_version="0.3.0"` | Version matches protocol v0.3 |
| `ServerConfig` | `url`, `token` | none | Bearer token for auth header |
| `QueueConfig` | none | `iq=4`, `frame=8` | Bounded queues per architecture |
| `TelemetryConfig` | none | `heartbeat=5s`, `status=10s` | Reasonable intervals |
| `ReconnectConfig` | none | `initial=1s`, `max=30s`, `factor=2.0`, `jitter=True` | Exponential backoff |
| `AgentConfig` | `identity`, `server`, `rf`, `iq` | `wire_encoding=JSON_BASE64`, sub-configs via `default_factory` | Root composition |

### Correctness

- All dataclasses frozen: verified by tests (`FrozenInstanceError` assertions).
- `default_factory` used for mutable sub-configs: verified by test asserting `cfg_a.queues is not cfg_b.queues`.
- `wire_encoding` defaults to `JSON_BASE64`: correct for MVP.

### Issues

- **No `stream_id` field anywhere in config.** The protocol requires `stream_id` (default `"default"` for MVP) in `stream_config`, `spectrum_frame`, and other messages. There is no place to configure or default this. The session implementation will need to source it — either hardcode `"default"` or add it to config.
- **No config validation.** `AgentConfig` accepts any values without checking constraints (e.g. `fft_size > 0`, `sample_rate_hz > 0`, `queue_size > 0`). The test plan (`agent_next_tests.md` section 5b) specifies 4 config validation tests that are not yet implemented.
- **No config loading.** There is no `from_file()`, `from_env()`, or CLI parser. Config objects must be constructed manually in code. This is fine for now but will need a loader before the agent can run standalone.

---

## 3. IQ Parser (`processing/parse_iq.py`)

### Error codes

| Code | Trigger | Implemented | Tested |
|------|---------|-------------|--------|
| `EMPTY_BUFFER` | `len(buffer) == 0` | Yes | Yes (via `test_parse_rejects_incomplete_sample` family) |
| `INCOMPLETE_SAMPLE` | `len(buffer) % bytes_per_sample != 0` | Yes | Yes |
| `UNSUPPORTED_FORMAT` | format not in supported set | Defined but unreachable | No (all 4 formats handled in `_decode_samples`) |
| `UNSUPPORTED_LAYOUT` | `layout != INTERLEAVED` | Yes | Not directly (Layout enum only has INTERLEAVED) |
| `INVALID_DESCRIPTOR` | missing required field | Defined but **no code path returns it** | No |

### Normalization

| Format | Operation | Spec | Implemented | Tested |
|--------|-----------|------|-------------|--------|
| float32 | direct copy | "clamp check only" | Copy without clamp | Yes (roundtrip test) |
| int16 | `raw / 32768.0` | `/ 32768.0` | Yes | Yes (exact values) |
| uint8 | `(raw - 127.5) / 127.5` | `(x - 127.5) / 127.5` | Yes | Yes (exact values + range check) |
| float64 | downcast to float32 | "downcast to f32 after normalize" | Yes (`.astype(np.float32)`) | Not directly (no float64-specific test) |

### DC removal

- Subtracts `mean(I)` from I channel, `mean(Q)` from Q channel, after normalization.
- Controlled by `descriptor.dc_offset_remove` (default `True`).
- Tested with synthetic biased data and with real LTE ci16_le fixture.
- Correct per spec.

### Endianness

- `_endian_char()` maps `LITTLE -> "<"`, `BIG -> ">"`.
- Applied via `np.dtype(...).newbyteorder(ec)`.
- Tested implicitly via the known-signal test (float32 LE) and the SigMF fixture (int16 LE).
- Big-endian path exists but has no dedicated test.

### Key invariants (from `iq_input_schema.md`)

| Invariant | Enforced | Tested |
|-----------|----------|--------|
| `len(samples) == sample_count * 2` | Yes (line 120) | Yes |
| `sample_count == len(buffer) / bytes_per_sample` | Yes (line 120) | Yes |
| `all(s >= -1.0) and all(s <= 1.0)` when normalized | Mostly (depends on format) | Yes (uint8, int16, real data) |
| `mean(I) ~= 0, mean(Q) ~= 0` when dc_offset_remove | Yes | Yes |
| Known-signal FFT peak lands in expected bin | N/A (parser only) | Yes (anchor test) |

### Issues

- **`INVALID_DESCRIPTOR` is dead code.** The error code is defined but `parse_iq()` never returns it. The function relies on Python's own `AttributeError`/`TypeError` for missing descriptor fields. Either add explicit validation or remove the dead code.
- **`UNSUPPORTED_FORMAT` is unreachable.** `_decode_samples` handles all 4 `SampleFormat` enum members and raises `ValueError` on anything else — but since `sample_format` is typed as `SampleFormat`, a non-member value can't arrive without a type violation. The `UNSUPPORTED_FORMAT` error code is never returned by `parse_iq`.
- **float32 has no clamp check.** The spec says float32 normalization is "clamp check only". Current code does a direct copy. If SDR hardware produces values outside `[-1.0, 1.0]`, they pass through to the FFT unchecked. This could produce unexpected dBFS values but is unlikely to cause incorrect behavior.
- **float64 normalization is just a downcast.** The spec says "downcast to f32 after normalize" but doesn't define what "normalize" means for float64 (no range is specified). Current behavior (direct downcast) is reasonable but could lose precision for extreme values. No float64-specific test exists.
- **Big-endian has no dedicated test.** The endianness code path exists and looks correct, but only little-endian is tested (via float32 LE and ci16_le fixture).

---

## 4. FFT Pipeline (`processing/fft_pipeline.py`)

### Pipeline steps

| Step | Implementation | Spec requirement | Correct |
|------|---------------|------------------|---------|
| 1. Split I/Q | `samples[0::2]`, `samples[1::2]` | Interleaved input | Yes |
| 2. Upcast to float64 | `.astype(np.float64)` | Internal precision | Yes |
| 3. Form complex | `i + 1j * q` | Complex FFT input | Yes |
| 4. Apply window | `complex_in * self._window` | Hann window | Yes |
| 5. FFT | `np.fft.fft(windowed)` | Standard FFT | Yes |
| 6. Normalize | `np.abs(fft_out) / window_norm` | Coherent window gain | Yes |
| 7. Power | `(...) ** 2` | Power spectrum | Yes |
| 8. fftshift | `np.fft.fftshift(power)` | `bin_order: low_to_high` | Yes |
| 9. Log scale | `10 * log10(max(power, 1e-12))` | dBFS, floor at -120 dB | Yes |
| 10. Cast to float32 | `.astype(np.float32)` | `numeric_type: float32` | Yes |
| 11. Pack to bytes | `.tobytes()` | float32 LE payload | Yes |

### Window normalization

- `window_norm = sum(window)` — this is the coherent gain of the Hann window.
- Dividing magnitude by this before squaring gives power relative to full scale (dBFS).
- Verified by test that compares output against a manual reference computation.

### Reconfiguration

- `configure()` replaces window and normalization factor immediately.
- Tested: fft_size change from 512 to 1024 takes effect on next `process()` call.

### Guard rails

- `process()` before `configure()` raises `RuntimeError`. Tested.
- Wrong sample count raises `ValueError`. Tested.

### Issues

- **`bin_count` is always `fft_size`.** The `SpectrumFrame` is created with `bin_count=config.fft_size`. The protocol allows `bin_count != fft_size` (e.g. frequency cropping), but for MVP they're equal. If a future `RFConfig` carries `bin_count`, the FFT pipeline would need to slice the output to `bin_count` bins before packing. Not a problem now.
- **Only Hann window is supported.** `_make_window` raises on anything else. `WindowFunction` enum only has `HANN`, so this is consistent, but the error path is untested.

---

## 5. IQ Processor (`processing/processor.py`)

### Responsibilities

1. Prepend byte remainder from previous chunk.
2. Align data to whole-sample boundary, hold trailing bytes.
3. Call `parse_iq` (stateless).
4. Accumulate float32 samples until `fft_size` complex samples are ready.
5. Feed to `FFTProcessor`, emit `SpectrumFrame`s.
6. `async run()`: drain `iq_queue`, push frames to `frame_queue`.

### Byte remainder handling

| Scenario | Behavior | Tested |
|----------|----------|--------|
| Chunk ends mid-sample | Trailing bytes held in `_remainder` | Yes |
| Next chunk arrives | Remainder prepended before alignment | Yes |
| Chunk smaller than `bytes_per_sample` | Entire chunk held as remainder | Yes |
| `configure()` called | Remainder cleared | Yes |

### Sample accumulation

| Scenario | Behavior | Tested |
|----------|----------|--------|
| Chunk has exactly `fft_size` samples | 1 frame emitted | Yes |
| Chunk has fewer than `fft_size` | 0 frames, samples buffered | Yes |
| Chunk has `2 * fft_size` samples | 2 frames emitted | Yes |
| Two half-chunks | 1 frame emitted on second push | Yes |
| 1.5x chunk then 0.5x chunk | 1 frame each | Yes |
| `configure()` called mid-accumulation | Buffer flushed, fresh start | Yes |

### Output frame properties

| Property | Value | Tested |
|----------|-------|--------|
| `bin_count` | `fft_size` | Yes |
| `payload` length | `bin_count * 4` | Yes |
| payload values | Finite float32 | Yes |
| `timestamp_utc` | Passed through from caller | Yes |

### `async run()` integration

- Reads from `iq_queue`, timestamps with `datetime.now(UTC)`, calls `push()`, writes to `frame_queue`.
- Runs until `CancelledError`.
- Tested with single-chunk and multi-chunk accumulation scenarios.

### Issues

- **`parse_iq` errors are silently dropped (line 76).** If `parse_iq` returns an `IQParseError`, the chunk is discarded with no logging or counter. The comment says "data should be well-formed" which is true after byte alignment, but silent drops make debugging harder. Consider incrementing a drop counter or logging at debug level.
- **Timestamp is set at dequeue time, not capture time.** In `run()`, `timestamp_utc` is `datetime.now(UTC)` when the chunk is pulled from the queue — not when the SDR captured it. The code documents this as "proxy for capture time in the absence of hardware timestamping at MVP". This is acceptable for MVP but means timestamp accuracy degrades under queue backpressure.
- **No backpressure on `frame_queue.put()`.** The `run()` method uses `await frame_queue.put(frame)` which blocks if the queue is full. This is correct behavior (bounded queue provides backpressure) but there is no drop counter or timeout — a full frame queue will stall the entire processing pipeline until the consumer (session) drains it.

---

## 6. SigMF Source (`source/sigmf.py`)

### SigMF datatype mapping

| SigMF datatype | SampleFormat | Endianness | Correct |
|----------------|-------------|------------|---------|
| `ci16_le` | INT16 | LITTLE | Yes |
| `ci16_be` | INT16 | BIG | Yes |
| `cf32_le` | FLOAT32 | LITTLE | Yes |
| `cf32_be` | FLOAT32 | BIG | Yes |
| `cf64_le` | FLOAT64 | LITTLE | Yes |
| `cf64_be` | FLOAT64 | BIG | Yes |
| `cu8_le` | UINT8 | LITTLE | Yes |
| `cu8_be` | UINT8 | LITTLE | Intentional (endianness irrelevant for single-byte) |

### Metadata parsing

- Reads `core:datatype` from `global`.
- Reads `core:sample_rate` from `global`.
- Reads `core:frequency` from `captures[0]`.
- Raises `UnsupportedSigMFDatatypeError` on unknown datatype.
- Raises `ValueError` on empty captures array.
- All fields produce a valid `IQDescriptor`.

### Block production (`run()`)

| Property | Behavior | Tested |
|----------|----------|--------|
| Output type | `bytes` | Yes |
| Block alignment | Rounded down to `bytes_per_sample` boundary | Yes |
| Block size | `<= block_size` parameter | Yes |
| Trailing partial sample | Trimmed before `put()` | Yes (alignment test) |
| Blocks parse without error | Confirmed with `parse_iq` | Yes |
| Parsed output dtype | float32 | Yes |
| Parsed output range | `[-1.0, 1.0]` | Yes |

### Issues

- **Blocking file I/O in async context.** `run()` uses `f.read(block_size)` inside a coroutine. For file I/O this is usually fast, but for very large files or slow storage it could block the event loop. Consider `asyncio.to_thread()` or `aiofiles` for production. Acceptable for MVP / dev / test use.
- **`stop()` is a no-op.** The file handle is opened inside `run()` with a context manager and closed when `run()` exits, so there's nothing to clean up. This is fine but means `stop()` can't be used to interrupt a running source — cancellation must come via task cancel.
- **No loop / repeat mode.** The source reads the file once and exits `run()`. For continuous testing or demo scenarios, a looping mode would be useful. Not a spec requirement.

---

## 7. Test Infrastructure

### Fixtures (`tests/conftest.py`)

| Fixture | Scope | Purpose |
|---------|-------|---------|
| `sigmf_lte_meta_path` | function | Path to trimmed LTE SigMF fixture (ci16_le, 847 MHz, 30.72 Msps) |
| `sigmf_lte_buffer` | function (async) | Descriptor + raw bytes via `SigMFSource` — for source-layer tests |
| `lte_ci16_raw` | function | Descriptor + raw bytes with hardcoded descriptor — for parser tests (source-independent) |

Fixture separation is correct: parser tests use `lte_ci16_raw` (no SigMFSource dependency), source tests use `sigmf_lte_meta_path`.

### Test file inventory

| File | Module under test | Test count | Sync/Async |
|------|-------------------|------------|------------|
| `test_iq_parser.py` | `parse_iq` | 26 | Mixed (sync + async for fixture tests) |
| `test_fft_pipeline.py` | `FFTProcessor` | 8 | Sync |
| `test_processor.py` | `IQProcessor` | 21 | Mixed (sync + 2 async for `run()`) |
| `test_sigmf_source.py` | `SigMFSource` | 13 | Async |
| `test_agent_config.py` | Config dataclasses | 17 | Sync |

### Test quality observations

- **Known-signal anchor test exists and is correct.** `test_parse_float32_interleaved_known_signal_peak_bin_matches_expected` generates a 100 kHz tone, parses it, runs FFT, and checks the peak bin. This is the most important parser test per the spec.
- **FFT reference test computes expected output manually.** `test_fft_processor_applies_hann_window_when_configured` builds the reference pipeline step-by-step and compares with `assert_array_almost_equal`. Strong.
- **Real-data tests use a CI-safe trimmed fixture.** The LTE uplink fixture is 256 KB (64k complex samples), small enough for CI.
- **Async tests use `create_task` + `cancel` + `CancelledError` pattern.** Correct for testing asyncio task lifecycle.

### Test gaps (within implemented modules only)

| Gap | Severity | Notes |
|-----|----------|-------|
| No big-endian parse test | Low | Code path exists, only LE tested |
| No float64 parse test | Low | Downcast path exists, untested |
| No `dc_offset_remove=False` preservation test | Low | Only `=True` tested with biased data |
| No `normalize=False` test for int16/uint8 | Low | Non-normalized path exists in code |
| No `EMPTY_BUFFER` error code assertion | Medium | Empty buffer tested in processor but not in parser directly |
| `UNSUPPORTED_FORMAT` unreachable | Medium | Dead error code |
| `INVALID_DESCRIPTOR` unreachable | Medium | Dead error code |
| No FFT unsupported window test | Low | Only Hann exists in enum |
| No IQProcessor error logging/counting test | Low | Silent drops untested |

---

## Summary of Issues (Implemented Code Only)

### Functional

1. **`INVALID_DESCRIPTOR` is dead code** — defined but never returned by `parse_iq()`.
2. **`UNSUPPORTED_FORMAT` is unreachable** — all enum members handled; non-members can't arrive.
3. **float32 has no clamp check** — spec says "clamp check only", code does direct copy.
4. **Silent drop of parse errors in `IQProcessor`** — no logging, no counter.

### Missing from domain model

5. **`RFConfig` has no `bin_count`** — needed for `stream_config` wire message.
6. **No `stream_id` in config** — needed for protocol messages.

### Test gaps

7. **Big-endian parse path untested.**
8. **float64 downcast path untested.**
9. **`EMPTY_BUFFER` has no direct parser test** (only tested indirectly via processor).
10. **`normalize=False` paths for int16/uint8 untested.**

### Acceptable for MVP

11. **Blocking file I/O in `SigMFSource.run()`** — fine for file sources, not for production SDR.
12. **Timestamp is dequeue time, not capture time** — documented trade-off.
13. **No config loading/validation** — manual construction only.
