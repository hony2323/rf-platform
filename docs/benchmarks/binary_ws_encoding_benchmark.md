# binary_ws vs json_base64 Encoding Benchmark

**Date:** 2026-04-08  
**Platform:** Windows 11 Pro, Python 3.14.3  
**Source:** Synthetic simulator (pure tone, unlimited rate)  
**Sample rate:** 2.4 MSPS, float32 IQ  
**Duration per run:** 30 s  
**Method:** `scripts/run_demo.py` → in-process fake server, no real network

---

## Summary

| fft_size | encoding     | total frames | avg fps | realtime fps needed | realtime ratio | ws MB/s (avg) | encode_send p50 ms |
|----------|--------------|--------------|---------|---------------------|----------------|---------------|--------------------|
| 512      | json_base64  | 116,938      | 3,898   | 4,688               | 0.83           | 11.1          | 0.030              |
| 512      | binary_ws    | 120,514      | 4,017   | 4,688               | 0.86           | 8.9           | 0.019              |
| 1024     | json_base64  | 59,974       | 1,999   | 2,344               | 0.85 (early)   | 11.7          | 0.043              |
| 1024     | binary_ws    | 59,118       | 1,971   | 2,344               | 0.87 (early)   | 8.5           | 0.027              |

---

## Throughput

### fft_size = 512

Both modes failed to sustain 4,688 fps real-time. json_base64 was steady at ~3,900 fps throughout. binary_ws started higher (~4,300 fps in the first 10 s) but became noisier in the second half, settling around ~3,800–4,000 fps. Over the full 30 s, binary_ws delivered **+3% more frames** (120,514 vs 116,938).

### fft_size = 1024

Both modes started strong (json_base64 ~2,200 fps, binary_ws ~2,350 fps in the first 10 s) then degraded. json_base64 dropped sharply at ~s17 and again at ~s22 (dipping to ~1,480 fps before recovering). binary_ws also degraded but more smoothly. Total frame counts are within 1.5% of each other (59,974 vs 59,118). **At 1024 bins both modes are CPU-saturated and throughput-equivalent within noise.**

### Interpretation

At 512 bins, per-frame overhead is a larger fraction of total cost. Skipping base64 and avoiding JSON payload wrapping gives binary_ws a measurable but small edge. At 1024 bins the FFT and parse_iq stages dominate; encoding savings are diluted and the results are effectively a tie.

Neither mode keeps up with real-time SDR throughput at these FFT sizes on this host in pure-Python asyncio. The bottleneck is CPU, not the encoding path.

---

## Wire Efficiency

Binary_ws sends **~24% fewer bytes** at every FFT size. This is the most consistent and significant difference between the two modes.

### Why

For a 1024-bin frame:

| component        | json_base64             | binary_ws               |
|------------------|-------------------------|-------------------------|
| payload bytes    | 4,096 raw → 5,464 base64| 4,096 raw (no encoding) |
| header/framing   | ~200 B JSON including payload key | ~200 B JSON (no payload) + 2 B length prefix |
| total per frame  | ~5,660 B                | ~4,298 B                |
| ratio            | 1.0×                    | **0.76×**               |

Observed:

| mode         | fft_size | avg ws MB/s |
|--------------|----------|-------------|
| json_base64  | 512      | 11.1        |
| binary_ws    | 512      | 8.9         |
| json_base64  | 1024     | 11.7        |
| binary_ws    | 1024     | 8.5         |

Reduction is consistent at **~24%** across both FFT sizes, matching the theoretical calculation.

---

## Encode-Send Latency

binary_ws is consistently **~40% faster to encode and send** than json_base64 at the same FFT size. This is the cleanest signal in the data — the difference holds across all pipeline_ms samples.

| fft_size | mode        | encode_send p50 ms | encode_send p99 ms |
|----------|-------------|--------------------|--------------------|
| 512      | json_base64 | 0.028–0.031        | 0.046–0.234        |
| 512      | binary_ws   | 0.018–0.023        | 0.060–0.167        |
| 1024     | json_base64 | 0.041–0.075        | 0.090–0.924        |
| 1024     | binary_ws   | 0.022–0.031        | 0.046–0.519        |

The p99 tails are large and variable in both modes — driven by OS scheduling and asyncio event loop jitter, not encoding work.

---

## Conclusions

1. **Wire bytes: clear win for binary_ws.** ~24% fewer bytes on every frame, every FFT size. This is a hard arithmetic result from removing base64, not a measurement artifact.

2. **Encode speed: clear win for binary_ws.** p50 encode-send is ~40% faster. Meaningful for low-latency single-channel streaming but diluted by FFT/parse costs at larger frames.

3. **Throughput (fps): a tie.** At 512 bins binary_ws edges ahead by ~3%; at 1024 the modes are within noise. Both are CPU-bound. Encoding is not the bottleneck in either case at these FFT sizes.

4. **Stability: json_base64 was slightly more consistent.** binary_ws had more run-to-run variance, particularly at 512 bins. Likely asyncio queue interaction, not encoding overhead.

---

## Caveats

- All runs were single-process loopback (agent + fake server in the same Python process). Real-network RTT would penalise larger frames more, which would widen the byte-efficiency advantage of binary_ws.
- CPU was saturated (95–100%) throughout. On a faster host or with lower-overhead SDR processing, the encode-send savings would be more impactful.
- The simulator generates IQ at unlimited rate. Rate-limited runs (e.g. `--rate-limit-msps 2.4`) would show both modes comfortably keeping up at real-time and the results would converge even more.
- Python 3.14 on Windows. Results on Linux with a real asyncio event loop may differ.

---

## Raw command

```bash
cd agent

# json_base64
python ../scripts/run_demo.py --fft-size 512  --duration 30 --encoding json_base64
python ../scripts/run_demo.py --fft-size 1024 --duration 30 --encoding json_base64

# binary_ws
python ../scripts/run_demo.py --fft-size 512  --duration 30 --encoding binary_ws
python ../scripts/run_demo.py --fft-size 1024 --duration 30 --encoding binary_ws
```

---

## uint8 IQ Benchmark

**Date:** 2026-04-08 / 2026-04-09  
**Platform:** Windows 11 Pro, Python 3.14.3  
**Source:** WAV file replay (`5829250000Hz_MWlamp_1250k.wav`)  
**IQ format:** uint8 (SDR RTL-style, 1 byte/sample component)  
**Sample rate:** 1.25 MSPS  
**Center freq:** 5829.25 MHz (MWlamp)  
**Duration per run:** 30 s  
**Method:** WAV file source (cycles on loop), in-process FakeAgentServer, no real network

Two sub-benchmarks:
- **Unlimited** — WAV read at full disk I/O speed (measures max throughput ceiling)
- **Rate-limited** — WAV throttled to 1.25 MSPS (measures performance at real hardware speed)

---

### Unlimited (max throughput)

| fft_size | encoding     | total frames | avg fps | realtime fps needed | realtime ratio | ws MB/s (avg) | encode_send p50 ms |
|----------|--------------|--------------|---------|---------------------|----------------|---------------|--------------------|
| 512      | json_base64  | 349,419      | 11,647  | 2,441               | ~4.8×          | ~35           | 0.019–0.038        |
| 512      | binary_ws    | 468,447      | 15,615  | 2,441               | ~6.4×          | ~34           | 0.008–0.009        |
| 1024     | json_base64  | 214,174      | 7,139   | 1,221               | ~5.8× (unstable)| ~39          | 0.032–0.043        |
| 1024     | binary_ws    | 307,471      | 10,249  | 1,221               | ~8.4×          | ~42           | 0.011–0.015        |

Both modes comfortably exceed real-time. binary_ws delivers **+34% more frames at 512 bins** and **+44% more at 1024 bins** because encode_send overhead is a meaningful fraction of per-frame CPU budget when the source runs unconstrained.

json_base64 at 1024 bins suffered a pronounced degradation window around s16–s22 (fps dropped to ~2,300 at worst). binary_ws was stable throughout — it sits further from the CPU saturation threshold.

---

### Rate-limited to 1.25 MSPS (real hardware speed)

`--rate-limit-msps 1.25` throttles the WAV source using a timestamp-based leaky bucket that self-corrects for sleep granularity. Effective source rate: ~1.24–1.26 MSPS (±1%), stable throughout.

| fft_size | encoding     | total frames | avg fps | realtime fps needed | realtime ratio | ws MB/s (avg) | encode_send p50 ms |
|----------|--------------|--------------|---------|---------------------|----------------|---------------|--------------------|
| 512      | json_base64  | 73,573       | ~2,435  | 2,441               | ~1.00          | ~6.9          | 0.024–0.036        |
| 512      | binary_ws    | 73,629       | ~2,438  | 2,441               | ~1.00          | ~5.4          | 0.010–0.011        |
| 1024     | json_base64  | 36,827       | ~1,219  | 1,221               | ~1.00          | ~6.7          | 0.045–0.053        |
| 1024     | binary_ws    | 36,806       | ~1,219  | 1,221               | ~1.00          | ~5.1          | 0.021–0.022        |

**Throughput is identical** — both modes deliver the same frame count because the source is rate-limited to what the hardware produces. The agent has comfortable headroom at every FFT size and encoding; both are stable with no drops.

**Wire bytes:** binary_ws sends **~24% fewer bytes** regardless of FFT size (same arithmetic result as float32 — base64 removal is independent of IQ format).

**Encode-send latency:** binary_ws p50 is ~50–70% lower (0.010–0.022 ms vs 0.024–0.053 ms). The gap is visible even though neither mode is the bottleneck at real-time speed.

**CPU usage:** 21–32% (vs 95–100% in the unlimited runs), confirming both modes have large processing headroom at 1.25 MSPS.

---

### parse_iq latency vs float32

uint8 parse_iq p50 is ~0.42–0.63 ms (rate-limited) vs ~0.033 ms for float32. The difference comes from the normalization formula: `(x - 127.5) / 127.5` per byte, which requires a subtraction and division for every sample. float32 input is already normalized to `[-1.0, 1.0]` and requires only a direct copy.

Note: p50 at rate-limited speed (~0.55 ms) is higher than at unlimited speed (~0.15 ms). At unlimited speed, the IQ queue is always full and chunks are processed back-to-back with minimal sleep; at rate-limited speed, each block arrives after a ~26 ms gap, so asyncio wakeup and scheduling overhead contribute to the measured latency.

---

### Conclusions

1. **At real hardware speed, encoding choice does not affect throughput.** Both deliver the same fps — the source is the bottleneck, not the encode path.

2. **binary_ws saves ~24% wire bytes at all FFT sizes.** This is consistent regardless of IQ format (the FFT output is always float32).

3. **binary_ws encodes ~50–70% faster** (p50). Meaningful for multi-channel or higher sample-rate scenarios where encode-send becomes a larger fraction of the budget.

4. **Unlimited throughput: binary_ws has a large advantage.** +34% at 512 bins, +44% at 1024 bins. json_base64 showed CPU saturation instability at 1024 bins that binary_ws avoided.

5. **Both modes have comfortable headroom at 1.25 MSPS.** CPU stays at 17–86% vs 95–100% at full speed.

---

### Raw command

```bash
cd agent

# uint8 WAV source — unlimited speed
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 512  --duration 30 --encoding json_base64
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 1024 --duration 30 --encoding json_base64
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 512  --duration 30 --encoding binary_ws
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 1024 --duration 30 --encoding binary_ws

# uint8 WAV source — rate-limited to real hardware speed
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 512  --duration 30 --encoding json_base64 --rate-limit-msps 1.25
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 1024 --duration 30 --encoding json_base64 --rate-limit-msps 1.25
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 512  --duration 30 --encoding binary_ws  --rate-limit-msps 1.25
python ../scripts/run_demo.py --wav "../recordings/5829250000Hz_MWlamp_1250k/5829250000Hz_MWlamp_1250k.wav" --freq 5829250000 --fft-size 1024 --duration 30 --encoding binary_ws  --rate-limit-msps 1.25
```
