import { useEffect, useRef } from "react";
import type { ViewerStreamConfigMessage, ViewerSpectrumFrameMessage } from "../types/viewer";
import { decodeFloat32Payload } from "../utils/fft";

const CANVAS_HEIGHT = 400;
const DBFS_MIN = -120;
const DBFS_MAX = 0;

interface WaterfallCanvasProps {
  config: ViewerStreamConfigMessage | null;
  onFrame: (cb: (frame: ViewerSpectrumFrameMessage) => void) => () => void;
}

export function WaterfallCanvas({ config, onFrame }: WaterfallCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const configRef = useRef<ViewerStreamConfigMessage | null>(null);

  // Reset canvas whenever config changes.
  useEffect(() => {
    configRef.current = config;
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (!config) return;
    canvas.width = config.rf.bin_count;
    canvas.height = CANVAS_HEIGHT;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "black";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }, [config]);

  // Subscribe to frames via the hook's onFrame callback.
  useEffect(() => {
    const unsub = onFrame((frame: ViewerSpectrumFrameMessage) => {
      const canvas = canvasRef.current;
      const cfg = configRef.current;
      if (!canvas || !cfg) return;
      if (frame.config_version !== cfg.config_version) return;

      const bins = decodeFloat32Payload(frame.data.payload, cfg.rf.bin_count);
      if (!bins) return;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      // Shift existing image down by 1 row.
      ctx.drawImage(canvas, 0, 1);

      // Build new top row.
      const imageData = ctx.createImageData(cfg.rf.bin_count, 1);
      const range = DBFS_MAX - DBFS_MIN;
      for (let i = 0; i < cfg.rf.bin_count; i++) {
        const clamped = Math.max(DBFS_MIN, Math.min(DBFS_MAX, bins[i]));
        const v = Math.round(((clamped - DBFS_MIN) / range) * 255);
        const idx = i * 4;
        imageData.data[idx] = v;
        imageData.data[idx + 1] = v;
        imageData.data[idx + 2] = v;
        imageData.data[idx + 3] = 255;
      }
      ctx.putImageData(imageData, 0, 0);
    });
    return unsub;
  }, [onFrame]);

  if (!config) {
    return (
      <div className="w-full h-[400px] bg-black flex items-center justify-center">
        <span className="text-gray-600 text-sm">Waiting for stream config…</span>
      </div>
    );
  }

  return (
    <canvas
      ref={canvasRef}
      className="w-full h-[400px] bg-black"
      style={{ imageRendering: "pixelated" }}
    />
  );
}
