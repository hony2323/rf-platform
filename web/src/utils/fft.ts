import type { ParsedFrame } from "@hony2323/waterfall-canvas";
import type {
  ViewerSpectrumFrameMessage,
  ViewerStreamConfigMessage,
} from "../types/viewer";

export const DBFS_MIN = -120;
export const DBFS_MAX = 0;
export const DBFS_RANGE = DBFS_MAX - DBFS_MIN;

export function toWaterfallFrame(
  frame: ViewerSpectrumFrameMessage,
  config: ViewerStreamConfigMessage,
): ParsedFrame | null {
  const raw = frame.data.payload;
  if (raw.length !== config.rf.bin_count) {
    if (import.meta.env.DEV) {
      console.error(
        `toWaterfallFrame: expected ${config.rf.bin_count} bins, got ${raw.length}`,
      );
    }
    return null;
  }

  // Normalize dBFS [-120, 0] → [0, 100] for the renderer (float32 precision expects 0–100).
  const normalized = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) {
    normalized[i] = Math.max(0, Math.min(100, ((raw[i] - DBFS_MIN) / DBFS_RANGE) * 100));
  }

  const centerHz = config.rf.center_freq_hz;
  return {
    header: [
      {
        band_id: "band_0",
        band_start: centerHz + config.rf.baseband_start_hz,
        band_end: centerHz + config.rf.baseband_end_hz,
        timestamp: frame.timestamp_utc,
        sent_at: Date.now(),
        length: normalized.byteLength,
        precision: "float32",
      },
    ],
    bands: { band_0: normalized },
  };
}

export const freqFormat = (hz: number) => (hz / 1e6).toFixed(3) + " MHz";
export const valueFormat = (t: number) =>
  (t * DBFS_RANGE + DBFS_MIN).toFixed(1) + " dBFS";
