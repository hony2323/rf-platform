# FFT Size Benchmark — UMTS O2 5 Downlink

**Date:** 2026-04-08  
**Recording:** `recordings/UMTS_O2_5_downlink/UMTS_O2_5_downlink.wav`  
**Format:** uint8 stereo (IQ), 5,000,000 sps, ~1 sec  
**Center freq (placeholder):** 882.4 MHz  
**Method:** `scripts/run_demo.py --wav ... --duration 5 --stats-hz 0.5`  
**Rate limiting:** none (file read at full disk speed — saturates pipeline)

---

## Results

| FFT size | Realtime FPS needed | Actual FPS | Realtime ratio | Can keep up | CPU% | WS MB/s |
|---|---|---|---|---|---|---|
| 256   | 19,531 | ~12,000 | 0.46 – 0.82 | **NO**          | 95% | 14 – 25 |
| 512   | 9,766  | ~10,100 | 0.98 – 1.29 | **BORDERLINE**  | 98% | 28 – 37 |
| 1024  | 4,883  | ~7,900  | 1.58 – 1.68 | YES             | 99% | 44 – 46 |
| 2048  | 2,441  | ~4,900  | 1.94 – 2.13 | YES             | 98% | 50 – 58 |
| 4096  | 1,221  | ~3,100  | 2.50 – 2.61 | YES             | 95% | 64 – 70 |
| 8192  | 610    | ~1,360  | 2.20 – 2.24 | YES             | 95% | 57 – 60 |

---

## Per-stage latency (p50 / p99 ms)

| FFT size | parse_iq p50 | parse_iq p99 | fft p50 | fft p99 | encode_send p50 | encode_send p99 |
|---|---|---|---|---|---|---|
| 256  | 0.19 | 0.60 | 0.02 | 0.04 | 0.01 | 0.04 |
| 512  | 0.16 | 0.83 | 0.12 | 0.25 | 0.06 | **10.68** |
| 1024 | 0.14 | 0.58 | 0.03 | 0.06 | 0.03 | 1.81 |
| 2048 | 0.15 | 1.08 | 0.05 | 0.18 | 0.07 | 2.28 |
| 4096 | 0.13 | 0.48 | 0.09 | 0.37 | 0.11 | 2.39 |
| 8192 | 0.28 | 1.14 | 0.20 | 0.51 | 0.35 | 3.30 |

---

## Queue depths (avg over run)

| FFT size | iq_queue (max 8) | frame_queue (max 16) |
|---|---|---|
| 256  | 8.0 | 7.3  |
| 512  | 8.0 | 10.4 |
| 1024 | 8.0 | 13.0 |
| 2048 | 8.0 | 14.5 |
| 4096 | 8.0 | 15.0 |
| 8192 | 8.0 | 15.5 |

The iq_queue is always full because the WAV file is read at disk speed with no rate limiting — this is expected and not indicative of a real SDR scenario. What matters is the realtime_ratio column.

---

## Findings

### FFT 256 — fails
At 5 Msps, 256-sample frames require 19,531 frames/sec. Frame-level overhead (queue operations, encode, WebSocket send) dominates the per-frame cost. The pipeline cannot drain samples fast enough; the iq_queue fills and the realtime ratio drops below 1.

### FFT 512 — borderline, avoid
Oscillates around the realtime threshold. The encode_send p99 spikes to **10.7ms** — clear evidence of queue pressure and CPU contention at saturation. Not safe for production use with a real SDR at 5 Msps.

### FFT 1024–4096 — sweet spot
Comfortable realtime headroom with stable p99 latencies. The FFT compute cost grows (~0.03ms → 0.09ms p50) but this is more than compensated by fewer frames to process per second.

- **FFT 1024** — 1.6× headroom, lowest per-frame latency, best time resolution
- **FFT 2048** — 1.9–2.1× headroom, good balance
- **FFT 4096** — peak headroom at 2.5×, best frequency resolution per bin

### FFT 8192 — diminishing returns
Headroom drops back to 2.2× (below 4096's 2.5×). The encode_send p50 triples to 0.35ms because each large frame takes significantly longer to base64-encode. The frame_queue depth climbs to 15.5 (near ceiling).

---

## Recommendation

**FFT 2048 or 4096** for UMTS at 5 Msps in production.

- Both provide reliable 2×+ realtime headroom.
- No drops observed in any run.
- FFT 2048: better time resolution, lower per-frame latency.
- FFT 4096: finer frequency resolution (~1.2 kHz/bin vs ~2.4 kHz/bin at 2048).

---

## Open questions / next steps

- [ ] Re-run with `--rate-limit-msps 5.0` to simulate true SDR pacing and see if queue dynamics change
- [ ] Test the LTE recording (30.72 Msps) — expected to fail at all but the largest FFT sizes
- [ ] Investigate the encode_send p99 spike at FFT 512 (WebSocket framing overhead?)
- [ ] Add window function variants (flat-top, blackman) to the benchmark
- [ ] Profile numpy FFT vs scipy FFT at large sizes (8192+)
