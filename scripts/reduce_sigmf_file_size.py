#!/usr/bin/env python3
"""Reduce a SigMF recording to the first N samples.

Usage:
    python reduce_file_size.py <folder> [--samples N] [--output <out_folder>]

Reads the .sigmf-meta to determine the correct dtype automatically.
Outputs a new folder with the truncated .sigmf-data and updated .sigmf-meta.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


# SigMF datatype string → numpy dtype
_DTYPE_MAP: dict[str, str] = {
    "cf32_le": "<f4", "cf32_be": ">f4",
    "ci32_le": "<i4", "ci32_be": ">i4",
    "ci16_le": "<i2", "ci16_be": ">i2",
    "ci8":     "i1",  "cu8":     "u1",
    "rf32_le": "<f4", "rf32_be": ">f4",
    "ri32_le": "<i4", "ri32_be": ">i4",
    "ri16_le": "<i2", "ri16_be": ">i2",
    "ri8":     "i1",  "ru8":     "u1",
}


def _find_pair(folder: Path) -> tuple[Path, Path]:
    data_files = list(folder.glob("*.sigmf-data"))
    meta_files = list(folder.glob("*.sigmf-meta"))
    if not data_files:
        raise FileNotFoundError(f"No .sigmf-data file in {folder}")
    if not meta_files:
        raise FileNotFoundError(f"No .sigmf-meta file in {folder}")
    if len(data_files) > 1:
        raise ValueError(f"Multiple .sigmf-data files in {folder}: {[f.name for f in data_files]}")
    return data_files[0], meta_files[0]


def reduce(folder: Path, n_samples: int, output: Path) -> None:
    data_path, meta_path = _find_pair(folder)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    datatype: str = meta["global"]["core:datatype"]

    if datatype not in _DTYPE_MAP:
        raise ValueError(f"Unsupported SigMF datatype: {datatype!r}. Supported: {list(_DTYPE_MAP)}")

    dtype = np.dtype(_DTYPE_MAP[datatype])
    is_complex = datatype.startswith("c")
    elems_per_sample = 2 if is_complex else 1

    raw = np.fromfile(data_path, dtype=dtype)
    total_samples = len(raw) // elems_per_sample

    if n_samples >= total_samples:
        print(f"Warning: file only has {total_samples:,} samples — no truncation needed.")
        n_samples = total_samples

    truncated = raw[: n_samples * elems_per_sample]

    output.mkdir(parents=True, exist_ok=True)
    out_data = output / data_path.name
    out_meta = output / meta_path.name

    truncated.tofile(out_data)
    size_kb = out_data.stat().st_size / 1024
    print(f"Wrote {n_samples:,} samples ({size_kb:,.1f} KB) → {out_data}")

    if "core:sample_count" in meta.get("global", {}):
        meta["global"]["core:sample_count"] = n_samples

    out_meta.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote metadata → {out_meta}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reduce a SigMF recording to the first N samples.")
    parser.add_argument("folder", type=Path, help="Folder with .sigmf-data and .sigmf-meta")
    parser.add_argument("--samples", type=int, default=100_000,
                        help="IQ samples to keep (default: 100000)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Output folder (default: <folder>_small)")
    args = parser.parse_args()

    folder = args.folder.resolve()
    if not folder.is_dir():
        print(f"Error: {folder} is not a directory", file=sys.stderr)
        sys.exit(1)

    output = args.output or folder.parent / (folder.name + "_small")
    reduce(folder, args.samples, output)


if __name__ == "__main__":
    main()
