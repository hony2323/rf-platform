"""RTL-SDR signal-path diagnostic.

Runs the same code path the agent uses (RTLSDRSource → parse_iq → FFT) and
prints stats at each stage so we can pinpoint whether weak signals are caused
by a normalization bug or by genuine SNR / gain issues.

Usage:
    uv run python scripts/diag_rtlsdr.py --freq 100000000 --gain 49.6
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import numpy as np

from agent.domain import (
    Endianness,
    IQDescriptor,
    Layout,
    RFConfig,
    SampleFormat,
    WindowFunction,
)
from agent.processing.fft_pipeline import FFTProcessor
from agent.processing.parse_iq import IQParseError, parse_iq
from agent.source.rtl_sdr_source import RTLSDRSource


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--freq", type=int, default=100_000_000)
    p.add_argument("--sample-rate", type=int, default=2_048_000)
    p.add_argument("--fft-size", type=int, default=1024)
    p.add_argument("--gain", default="auto")
    p.add_argument("--n-frames", type=int, default=5)
    args = p.parse_args()

    src = RTLSDRSource(
        center_freq_hz=args.freq,
        sample_rate_hz=args.sample_rate,
        gain=args.gain,
        chunk_samples=args.fft_size,
        fps=10.0,
    )
    await src.start()

    # Build the same descriptor + RFConfig the CLI builds for RTL-SDR.
    descriptor = IQDescriptor(
        sample_format=SampleFormat.FLOAT32,
        endianness=Endianness.LITTLE,
        layout=Layout.INTERLEAVED,
        sample_rate_hz=args.sample_rate,
        center_freq_hz=args.freq,
    )
    rf = RFConfig(
        center_freq_hz=args.freq,
        sample_rate_hz=args.sample_rate,
        fft_size=args.fft_size,
        window_fn=WindowFunction.HANN,
    )
    fft = FFTProcessor()
    fft.configure(rf)

    print(
        f"\nDescriptor: format={descriptor.sample_format.value}, "
        f"normalize={descriptor.normalize}, "
        f"dc_offset_remove={descriptor.dc_offset_remove}\n"
    )

    queue: asyncio.Queue[bytes] = asyncio.Queue()
    runner = asyncio.create_task(src.run(queue))

    try:
        for n in range(args.n_frames):
            raw = await queue.get()

            # 1. Raw bytes → reinterpret as float32 (this is what the source emits)
            raw_f32 = np.frombuffer(raw, dtype=np.float32)
            print(f"--- frame {n + 1} ---")
            print(
                f"  source bytes: len={len(raw)}, "
                f"min={raw_f32.min():+.4f} max={raw_f32.max():+.4f} "
                f"mean={raw_f32.mean():+.4f} std={raw_f32.std():.4f}"
            )

            # 2. parse_iq (clip + DC removal)
            res = parse_iq(descriptor, raw)
            if isinstance(res, IQParseError):
                print(f"  parse_iq ERROR: {res.message}")
                continue
            s = res.samples
            i, q = s[0::2], s[1::2]
            print(
                f"  parsed I:     mean={i.mean():+.4f} std={i.std():.4f}"
                f"  Q: mean={q.mean():+.4f} std={q.std():.4f}"
            )

            # 3. FFT
            frame = fft.process(s, "1970-01-01T00:00:00Z")
            db = np.frombuffer(frame.payload, dtype=np.float32)
            peak_idx = int(np.argmax(db))
            peak_freq_offset = (peak_idx - args.fft_size // 2) * (
                args.sample_rate / args.fft_size
            )
            print(
                f"  fft dBFS:     min={db.min():.1f} max={db.max():.1f} "
                f"median={float(np.median(db)):.1f}  "
                f"peak bin={peak_idx} ({peak_freq_offset / 1e3:+.0f} kHz from centre)"
            )
    finally:
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass
        await src.stop()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
