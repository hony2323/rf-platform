#!/usr/bin/env python3
"""Reduce a WAV recording to the first N frames.

Usage:
    python reduce_wav_file_size.py <file.wav> [--frames N] [--output <out.wav>]

Reads the WAV header to determine sample rate, channels, and bit depth
automatically. Outputs a new WAV file with the truncated audio data and
updated header.

Note: a WAV *frame* is one sample across all channels (e.g. for stereo IQ,
one frame = one I/Q pair). This matches the terminology used by Python's
`wave` module (`getnframes`, `readframes`).
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path


def truncate_wav(input_path: Path, n_frames: int, output_path: Path) -> None:
    """Copy the first n_frames frames of input_path into output_path."""
    with wave.open(str(input_path), "rb") as src:
        n_channels = src.getnchannels()
        sampwidth = src.getsampwidth()
        framerate = src.getframerate()
        total_frames = src.getnframes()

        if n_frames >= total_frames:
            print(
                f"Warning: file only has {total_frames:,} frames — "
                "no truncation needed."
            )
            n_frames = total_frames

        frames = src.readframes(n_frames)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(output_path), "wb") as dst:
        dst.setnchannels(n_channels)
        dst.setsampwidth(sampwidth)
        dst.setframerate(framerate)
        dst.writeframes(frames)

    size_kb = output_path.stat().st_size / 1024
    print(
        f"Wrote {n_frames:,} frames "
        f"({n_channels}ch, {framerate:,} Hz, {sampwidth * 8}-bit, "
        f"{size_kb:,.1f} KB) → {output_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reduce a WAV recording to the first N frames."
    )
    parser.add_argument("file", type=Path, help="Input .wav file")
    parser.add_argument(
        "--frames",
        type=int,
        default=100_000,
        help="Frames to keep (default: 100000)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output .wav file (default: <stem>_small.wav alongside the input)",
    )
    args = parser.parse_args()

    if args.frames < 0:
        parser.error("--frames must be a non-negative integer")

    input_path = args.file.resolve()
    if not input_path.is_file():
        print(f"Error: {input_path} is not a file", file=sys.stderr)
        sys.exit(1)
    if input_path.suffix.lower() != ".wav":
        print(f"Error: {input_path} does not have a .wav extension", file=sys.stderr)
        sys.exit(1)

    output_path = (
        args.output.resolve()
        if args.output is not None
        else input_path.with_stem(input_path.stem + "_small")
    )

    if output_path == input_path:
        parser.error("--output must not resolve to the same path as the input file")

    try:
        truncate_wav(input_path, args.frames, output_path)
    except (wave.Error, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
