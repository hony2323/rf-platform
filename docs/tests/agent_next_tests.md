# Agent next tests

This is the practical test plan for the next implementation steps in `agent/`.

The goal is to lock down the frozen contracts first:
1. IQ parsing
2. protocol codec
3. session handshake and streaming gating
4. FFT pipeline
5. small integration flows

---

## 1) `processing/iq_parser.py` — unit tests

Write these first.

### `test_parse_float32_interleaved_known_signal_peak_bin_matches_expected`
Purpose:
- validates byte order
- validates interleaving
- validates parser output is usable by FFT
- validates expected fftshifted peak position

Arrange:
- generate pure tone I/Q
- encode as float32, little-endian, interleaved
- parse with descriptor
- feed parsed samples into FFT

Assert:
- peak bin equals expected fftshifted bin

Notes:
- this is the anchor parser test
- use the exact known-signal idea from the frozen IQ schema

### `test_parse_float32_roundtrip_values_preserved`
Arrange:
- build float32 interleaved IQ buffer with known values (no normalization math needed)

Assert:
- parsed output values match input values exactly
- output dtype is float32
- catches byte-order bugs without FFT machinery

### `test_parse_int16_normalizes_using_divide_by_32768`
Arrange:
- build a tiny int16 interleaved IQ buffer with known values

Assert:
- parsed values equal `raw / 32768.0`
- output is float32
- shape is correct

### `test_parse_uint8_normalizes_using_center_and_scale`
Arrange:
- build small uint8 interleaved IQ buffer

Assert:
- parsed values equal `(x - 127.5) / 127.5`
- values stay within `[-1.0, 1.0]`

### `test_parse_float64_downcasts_to_float32_after_normalize`
Arrange:
- float64 input buffer

Assert:
- output dtype is float32
- values are preserved within tolerance

### `test_parse_applies_dc_offset_removal_when_enabled`
Arrange:
- build biased I/Q values with non-zero channel mean
- descriptor has `dc_offset_remove=True`

Assert:
- mean of I channel is near zero
- mean of Q channel is near zero

### `test_parse_skips_dc_offset_removal_when_disabled`
Arrange:
- same biased data
- descriptor has `dc_offset_remove=False`

Assert:
- channel means are still biased

### `test_parse_rejects_empty_buffer`
Assert:
- returns or raises `EMPTY_BUFFER`

### `test_parse_rejects_incomplete_sample_for_non_multiple_of_bytes_per_sample`
Assert:
- returns or raises `INCOMPLETE_SAMPLE`

### `test_parse_output_length_matches_sample_count_times_two`
Assert:
- `len(samples) == sample_count * 2`

### `test_parse_chunk_boundary_requires_caller_to_supply_remainder`
Arrange:
- valid chunk plus partial trailing sample

Assert:
- first parse returns `INCOMPLETE_SAMPLE`
- second parse with remainder succeeds

---

## 2) `protocol/codec.py` — unit tests

These should assert on decoded values, not raw encoded text details.

### `test_encode_decode_connect_roundtrip`
Assert:
- `msg_type`
- `protocol_version`
- `node_id`
- `agent_version`
- `requested_encoding`
- optional `hardware` survives roundtrip

### `test_encode_decode_connect_ack_roundtrip`
Assert:
- `session_id`
- `status`
- `wire_encoding`

### `test_encode_decode_stream_config_roundtrip`
Assert:
- `node_id`
- `session_id`
- `stream_id`
- RF fields
- FFT semantics fields
- advisory and authoritative fields preserved correctly

### `test_encode_decode_stream_config_ack_roundtrip`
Assert:
- `session_id`
- `stream_id`
- `config_version`
- `status`

### `test_encode_decode_spectrum_frame_roundtrip_decodes_payload_back_to_original_bytes`
Assert:
- payload bytes after decode exactly match original bytes
- `frame_index`
- `config_version`
- `stream_id`
- `session_id`

### `test_encode_decode_heartbeat_roundtrip`
Assert:
- fields survive roundtrip

### `test_encode_decode_agent_status_roundtrip`
Assert:
- metrics survive roundtrip
- nested `drops` survive roundtrip

### `test_encode_decode_disconnect_roundtrip`
Assert:
- `session_id`
- `reason`

### `test_encode_decode_error_roundtrip_with_optional_fields_present`
Assert:
- `stream_id`
- `config_version`
- `frame_index`
- `code`
- `fatal`

### `test_encode_decode_error_roundtrip_with_optional_fields_omitted`
Assert:
- omitted optional fields stay omitted or decode to `None`

### `test_decode_rejects_unknown_msg_type`
Assert:
- decoder fails fast on unknown message type

### `test_decode_rejects_invalid_json`
Assert:
- invalid payload is rejected cleanly

### `test_decode_rejects_message_missing_required_fields`
Assert:
- malformed message fails validation

### `test_json_base64_payload_length_matches_bin_count_times_four_after_decode`
Arrange:
- create frame with `bin_count` worth of float32 bytes

Assert:
- decoded payload length matches expected byte count

---

## 3) `session/manager.py` — unit tests with fake transport

Do these with a fake transport and fake codec. No real websocket yet.

### `test_session_starts_in_disconnected_state`
Assert:
- initial state is `DISCONNECTED`

### `test_session_moves_to_connecting_when_run_starts`
Assert:
- state becomes `CONNECTING` before handshake completes

### `test_session_sends_connect_first_after_transport_connects`
Assert:
- first outbound message is `connect`

### `test_session_stores_session_id_from_transport_header_before_connect_ack`
Arrange:
- fake transport exposes `X-Session-Id`

Assert:
- session captures header session id

### `test_session_moves_to_connected_on_connect_ack`
Assert:
- state becomes `CONNECTED`
- ack session id is stored and validated

### `test_session_rejects_connect_ack_with_mismatched_session_id`
Assert:
- session fails if header `session_id` and `connect_ack.session_id` differ

### `test_session_sends_stream_config_after_connect_ack`
Assert:
- second outbound message is `stream_config`

### `test_session_moves_to_configured_on_stream_config_ack`
Assert:
- state becomes `CONFIGURED`
- `config_version` stored

### `test_session_rejects_stream_config_ack_for_wrong_stream_id`
Assert:
- session fails on mismatch

### `test_session_blocks_frame_send_until_configured`
Arrange:
- put frame in queue before handshake completes

Assert:
- no `spectrum_frame` is sent before `stream_config_ack`

### `test_session_enters_streaming_when_configured_and_frame_available`
Assert:
- state becomes `STREAMING`
- frame is sent

### `test_session_attaches_session_stream_and_config_fields_to_outbound_frame`
Assert:
- sent frame has:
  - `session_id`
  - `stream_id`
  - `config_version`

### `test_session_initial_frame_index_starts_at_zero`
Assert:
- first sent frame has `frame_index == 0`

### `test_session_increments_frame_index_per_frame`
Assert:
- frame indices go `0, 1, 2, ...`

### `test_session_resets_frame_index_after_config_update`
Arrange:
- send frames
- trigger config update
- receive new `stream_config_ack`

Assert:
- next frame index returns to `0`

### `test_session_handles_nonfatal_server_error_without_closing_session`
Arrange:
- receive `error` with `fatal=false`

Assert:
- session stays alive

### `test_session_stops_on_fatal_server_error`
Arrange:
- receive `error` with `fatal=true`

Assert:
- session exits
- runtime will later be responsible for reconnect

### `test_session_stops_on_disconnect_message`
Arrange:
- receive `disconnect`

Assert:
- session exits cleanly

### `test_session_stops_if_transport_send_fails`
Assert:
- send failure ends current session run

### `test_session_stops_if_transport_recv_fails`
Assert:
- recv failure ends current session run

### `test_session_request_config_update_causes_new_stream_config_send`
Only if you keep `request_config_update()` now.

Assert:
- updated config is sent
- waits for new ack
- new config version replaces old one

### `test_session_does_not_consume_stale_session_identifiers_after_reconnect`
Arrange:
- run one session
- stop
- start a new session

Assert:
- new run uses new `session_id`
- old state is gone

---

## 4) `processing/fft_pipeline.py` — unit tests

Do these after parser and session handshake basics exist.

### `test_fft_processor_requires_configure_before_process`
Assert:
- calling `process()` before `configure()` fails clearly

### `test_fft_processor_output_payload_length_equals_bin_count_times_four`
Assert:
- returned packed payload length is exactly `bin_count * 4`

### `test_fft_processor_applies_hann_window_when_configured`
Arrange:
- use a predictable signal and inspect output against a reference implementation

Assert:
- output matches configured window behavior

### `test_fft_processor_produces_log_power_float32_payload`
Assert:
- unpacked payload is float32
- values look like log power, not raw complex FFT output

### `test_fft_processor_outputs_bins_in_low_to_high_order`
Arrange:
- known tone

Assert:
- peak lands where expected after fftshift ordering

### `test_fft_processor_handles_bin_count_less_than_fft_size_if_supported_by_contract`
Arrange:
- config where `bin_count != fft_size`

Assert:
- output still matches configured `bin_count`

### `test_fft_processor_timestamp_is_capture_start_not_processing_end`
If timestamp is set in this layer.

Assert:
- timestamp semantics are correct and stable

### `test_fft_processor_reconfigure_changes_output_shape_and_resets_internal_state`
Assert:
- new config takes effect immediately
- no stale buffer semantics leak through

---

## 5) `telemetry/` — unit tests

### `test_metrics_collector_snapshot_reports_queue_depth_fill_and_tx_rate`
Assert:
- snapshot contains expected values

### `test_metrics_collector_resets_drop_counters_after_snapshot`
Because your schema says counts are since last `agent_status`.

Assert:
- after snapshot, counters return to zero

### `test_metrics_collector_keeps_non_drop_metrics_available_after_reset`
Assert:
- only drop counters reset, not current gauges

### `test_telemetry_emits_heartbeat_on_schedule`
Use fake clock if possible.

Assert:
- heartbeat message emitted periodically
- contains `node_id` and current `session_id`

### `test_telemetry_emits_agent_status_on_schedule`
Assert:
- agent status emitted periodically
- includes queue depth / fill / drops snapshot

### `test_telemetry_does_not_emit_when_session_state_disallows_it`
Arrange:
- session state is CONNECTING or CONNECTED (not yet STREAMING)

Assert:
- no heartbeat or status messages are sent

---

## 5b) `config/` — validation unit tests

These validate constraints that the type system alone doesn't catch.
Moved here from the parser tests — `IQDescriptor` and `SampleFormat` are
constructed at the config boundary, not inside the parser.

### `test_config_rejects_unsupported_sample_format_string`
Arrange:
- raw config dict with `sample_format: "bfloat16"`

Assert:
- config loading raises a clear validation error

### `test_config_rejects_unsupported_layout_string`
Arrange:
- raw config dict with `layout: "planar"`

Assert:
- config loading raises a clear validation error

### `test_config_rejects_missing_sample_rate_hz`
Assert:
- config loading raises on missing required field

### `test_config_rejects_missing_center_freq_hz`
Assert:
- config loading raises on missing required field

---

## 6) `transport/` — unit tests

If transport remains a wrapper around a websocket client, keep tests small.

### `test_transport_extracts_session_id_from_upgrade_header`
Assert:
- `X-Session-Id` is captured correctly

### `test_transport_connect_uses_bearer_token_in_authorization_header`
Assert:
- correct header is sent

### `test_transport_send_text_delegates_to_underlying_ws_client`
### `test_transport_recv_text_returns_text_message`
### `test_transport_close_closes_underlying_ws_client`

Do not over-test the websocket library itself.

---

## 7) Integration tests inside `agent/tests/integration/`

Keep these thin and valuable.

### `test_session_handshake_happy_path_with_fake_server`
Arrange:
- fake server accepts websocket
- returns upgrade header session id
- sends `connect_ack`
- sends `stream_config_ack`

Assert:
- agent reaches `CONFIGURED` or `STREAMING`

### `test_session_streams_frame_after_successful_handshake`
Arrange:
- frame queue has one frame

Assert:
- fake server receives `spectrum_frame` with:
  - correct `session_id`
  - correct `config_version`
  - `frame_index == 0`

### `test_session_sends_heartbeat_and_status_during_streaming`
Assert:
- fake server sees telemetry messages while stream is active

### `test_session_stops_and_runtime_can_restart_after_disconnect`
This is the first runtime-ish integration worth doing later.

Assert:
- first session ends on disconnect
- second run establishes a fresh session

### `test_nonfatal_error_does_not_stop_streaming_but_fatal_error_does`
Assert:
- `fatal=false` keeps session alive
- `fatal=true` tears it down

---

## 8) Suggested first batch to actually implement now

Do not try to write all tests in one go.

### Batch 1
- `test_parse_float32_interleaved_known_signal_peak_bin_matches_expected`
- `test_parse_float32_roundtrip_values_preserved`
- `test_parse_int16_normalizes_using_divide_by_32768`
- `test_parse_uint8_normalizes_using_center_and_scale`
- `test_parse_applies_dc_offset_removal_when_enabled`
- `test_parse_rejects_incomplete_sample`

### Batch 2
- `test_encode_decode_connect_roundtrip`
- `test_encode_decode_stream_config_roundtrip`
- `test_encode_decode_spectrum_frame_roundtrip_decodes_payload_back_to_original_bytes`
- `test_decode_rejects_unknown_msg_type`

### Batch 3
- `test_session_sends_connect_first_after_transport_connects`
- `test_session_moves_to_connected_on_connect_ack`
- `test_session_sends_stream_config_after_connect_ack`
- `test_session_moves_to_configured_on_stream_config_ack`
- `test_session_blocks_frame_send_until_configured`
- `test_session_initial_frame_index_starts_at_zero`
- `test_session_increments_frame_index_per_frame`

That is the correct first push.

---

## 9) Naming suggestion

Put them roughly here:

```text
tests/
  unit/
    processing/
      test_iq_parser.py
      test_fft_pipeline.py
    protocol/
      test_codec.py
    session/
      test_session_manager.py
    telemetry/
      test_metrics_collector.py
      test_telemetry.py
    transport/
      test_transport.py
  integration/
    session/
      test_handshake_flow.py
      test_streaming_flow.py
```

---

## 10) Final note

The first real win is not “agent runtime works”.
The first real win is:

- parser invariants locked
- codec roundtrips locked
- session handshake order locked

After that, implementation becomes mostly plumbing.
