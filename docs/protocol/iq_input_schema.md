# IQ Input Schema (MVP, frozen)

Parser contract: accepts `(descriptor, bytes)`. Source-agnostic — file, USB SDR, TCP socket, memory buffer are all resolved above this layer.

---

## Descriptor

```json
{
  "sample_format":    "float32",
  "endianness":       "little",
  "layout":           "interleaved",
  "sample_rate_hz":   2400000,
  "center_freq_hz":   433920000,
  "dc_offset_remove": true,
  "normalize":        true
}
```

---

## Field authority

| Field | Status | Default | Notes |
|---|---|---|---|
| `sample_format` | required | — | float32 \| int16 \| float64 \| uint8 |
| `endianness` | required | — | little \| big |
| `layout` | required | — | interleaved only (MVP) |
| `sample_rate_hz` | required | — | must match stream_config. No inference. |
| `center_freq_hz` | required | — | must match stream_config. No inference. |
| `dc_offset_remove` | optional | true | subtract mean(I), mean(Q) after normalize |
| `normalize` | optional | true | scale to [-1.0, 1.0]. must be true for FFT pipeline |
| `bytes_per_sample` | derived | — | = 2 × sizeof(sample_format). parser computes. |
| `sample_count` | derived | — | = buffer_byte_length / bytes_per_sample. parser computes. |

---

## Sample formats

| format | dtype | bytes/sample | raw range | normalize op |
|---|---|---|---|---|
| `float32` | f32 × 2 | 8 | hardware-dependent | clamp check only |
| `int16` | i16 × 2 | 4 | [-32768, 32767] | `/ 32768.0` |
| `uint8` | u8 × 2 | 2 | [0, 255] | `(x - 127.5) / 127.5` |
| `float64` | f64 × 2 | 16 | hardware-dependent | downcast to f32 after normalize |

Default: `float32`.

---

## Memory layout — interleaved (MVP only)

```
bytes:    [I₀][Q₀][I₁][Q₁][I₂][Q₂]...
complex:   sample 0    sample 1    sample 2

complex[n].real = buffer[n*2]
complex[n].imag = buffer[n*2 + 1]
sample_count    = byte_length / bytes_per_sample
```

Planar layout (`IIII...QQQQ...`) is post-MVP. Add `"layout": "planar"` to descriptor when needed.

---

## Parser input / output

```
INPUT
  descriptor : IQDescriptor
  buffer     : bytes          # raw IQ bytes, length >= bytes_per_sample

OUTPUT (success)
  samples    : float32[]      # interleaved complex, length = sample_count × 2
                              # normalized to [-1.0, 1.0]
                              # dc offset removed if dc_offset_remove=true

OUTPUT (error)
  error.code    : string
  error.message : string
  error.offset  : int | null  # byte offset of failure, if applicable
```

Normalization and DC removal are the parser's responsibility. The FFT module always receives float32 in `[-1.0, 1.0]` — it never sees raw int16 or uint8.

DC removal: subtract `mean(I)` and `mean(Q)` from respective channels, applied after normalization.

---

## Chunk / streaming boundary

The parser is stateless. It does not buffer across calls. If a chunk ends mid-sample, the caller must hold the remainder bytes and prepend them to the next chunk. The parser returns `INCOMPLETE_SAMPLE` for any buffer whose length is not a multiple of `bytes_per_sample`.

---

## Error codes

| Code | When |
|---|---|
| `EMPTY_BUFFER` | `len(buffer) == 0` |
| `INCOMPLETE_SAMPLE` | `len(buffer) % bytes_per_sample != 0` |
| `UNSUPPORTED_FORMAT` | `sample_format` not in supported set |
| `UNSUPPORTED_LAYOUT` | `layout != "interleaved"` (MVP) |
| `INVALID_DESCRIPTOR` | missing required field |

---

## Test invariants

```python
# output shape
len(samples) == sample_count * 2
sample_count == len(buffer) / bytes_per_sample
bytes_per_sample == { "float32": 8, "int16": 4, "float64": 16, "uint8": 2 }[sample_format]

# normalization bounds
all(s >= -1.0 for s in samples)
all(s <=  1.0 for s in samples)

# normalization correctness
# int16:   normalized = raw / 32768.0
# uint8:   normalized = (raw - 127.5) / 127.5
# float64: downcast to float32 after normalize

# dc offset removal (dc_offset_remove=true)
abs(mean(samples[0::2])) < epsilon   # I channel mean ≈ 0
abs(mean(samples[1::2])) < epsilon   # Q channel mean ≈ 0

# interleaving
complex[n].real == samples[n * 2]
complex[n].imag == samples[n * 2 + 1]

# error cases
len(buffer) == 0                     → EMPTY_BUFFER
len(buffer) % bytes_per_sample != 0  → INCOMPLETE_SAMPLE
sample_format not in supported set   → UNSUPPORTED_FORMAT
layout != "interleaved"              → UNSUPPORTED_LAYOUT
missing sample_rate_hz               → INVALID_DESCRIPTOR
missing center_freq_hz               → INVALID_DESCRIPTOR

# chunk boundary
buffer = valid_chunk + partial_sample_bytes
parse(descriptor, buffer)            → INCOMPLETE_SAMPLE
parse(descriptor, buffer + remainder) → ok
```

---

## Known-signal test (write first)

The most important parser test. Validates normalization, DC removal, interleaving, and byte order in one shot.

```python
# pure tone at frequency f_tone
f_tone       = 100_000          # Hz
sample_rate  = 2_400_000        # Hz
n_samples    = 131_072
t            = [i / sample_rate for i in range(n_samples)]
I            = [cos(2π * f_tone * t[i]) * 0.5 for i in range(n_samples)]
Q            = [sin(2π * f_tone * t[i]) * 0.5 for i in range(n_samples)]
buffer       = interleave_and_encode(I, Q, format="float32")

samples      = parse(descriptor, buffer)
fft_out      = fft(samples)

expected_bin = round(f_tone / bin_size_hz) + fft_size // 2  # fftshift
peak_bin     = argmax(abs(fft_out))

assert peak_bin == expected_bin
```

---

## Post-MVP additions (do not implement now)

- `"layout": "planar"` support
- `"file_format": "sigmf" | "wav" | "bin"` — header sniffing for known container formats
- `gain_db` — SDR hardware gain for dBm calibration
- Metadata inference from file headers (SigMF, GNU Radio)
