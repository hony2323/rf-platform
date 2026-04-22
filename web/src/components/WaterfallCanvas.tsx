import { useEffect, useRef } from "react";
import {
  WaterfallCanvas as WaterfallLib,
  type WaterfallCanvasHandle,
} from "@hony2323/waterfall-canvas/react";
import { interpolateTurbo } from "@hony2323/waterfall-canvas";
import type {
  ViewerSpectrumFrameMessage,
  ViewerStreamConfigMessage,
} from "../types/viewer";
import { DBFS_MIN, DBFS_RANGE, toWaterfallFrame, freqFormat, valueFormat } from "../utils/fft";

interface WaterfallCanvasProps {
  config: ViewerStreamConfigMessage | null;
  onFrame: (cb: (frame: ViewerSpectrumFrameMessage) => void) => () => void;
  /** Bottom of the visible dBFS window. Default: DBFS_MIN (-120). */
  dbfsFloor?: number;
  /** Top of the visible dBFS window. Default: 0. */
  dbfsCeiling?: number;
}

/**
 * Bridges touch input to the mouse/wheel events the underlying
 * waterfall-canvas library listens for.
 *   - 1-finger drag → mousedown/mousemove/mouseup (pan)
 *   - 2-finger pinch → wheel (zoom, centered between fingers)
 */
function useTouchBridge(containerRef: React.RefObject<HTMLDivElement | null>, deps: unknown[]) {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const canvas = container.querySelector("canvas");
    if (!canvas) return;

    canvas.style.touchAction = "none";

    let mode: "none" | "pan" | "pinch" = "none";
    let lastPinchDist = 0;

    const fireMouse = (type: string, x: number, y: number) => {
      canvas.dispatchEvent(
        new MouseEvent(type, {
          clientX: x,
          clientY: y,
          bubbles: true,
          cancelable: true,
          button: 0,
          buttons: type === "mouseup" ? 0 : 1,
        }),
      );
    };

    const fireWheel = (x: number, y: number, deltaY: number) => {
      canvas.dispatchEvent(
        new WheelEvent("wheel", {
          clientX: x,
          clientY: y,
          deltaY,
          bubbles: true,
          cancelable: true,
        }),
      );
    };

    const onStart = (e: TouchEvent) => {
      if (e.touches.length === 1) {
        mode = "pan";
        const t = e.touches[0];
        fireMouse("mousedown", t.clientX, t.clientY);
        e.preventDefault();
      } else if (e.touches.length === 2) {
        if (mode === "pan") {
          const t = e.changedTouches[0] ?? e.touches[0];
          fireMouse("mouseup", t.clientX, t.clientY);
        }
        mode = "pinch";
        const [a, b] = [e.touches[0], e.touches[1]];
        lastPinchDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        e.preventDefault();
      }
    };

    const onMove = (e: TouchEvent) => {
      if (mode === "pan" && e.touches.length === 1) {
        const t = e.touches[0];
        fireMouse("mousemove", t.clientX, t.clientY);
        e.preventDefault();
      } else if (mode === "pinch" && e.touches.length >= 2) {
        const [a, b] = [e.touches[0], e.touches[1]];
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const cx = (a.clientX + b.clientX) / 2;
        const cy = (a.clientY + b.clientY) / 2;
        if (lastPinchDist > 0 && dist > 0) {
          // fingers apart (ratio > 1) = zoom in = negative deltaY (wheel up)
          const deltaY = -Math.log(dist / lastPinchDist) * 200;
          if (Number.isFinite(deltaY) && deltaY !== 0) fireWheel(cx, cy, deltaY);
        }
        lastPinchDist = dist;
        e.preventDefault();
      }
    };

    const onEnd = (e: TouchEvent) => {
      if (mode === "pan") {
        const t = e.changedTouches[0];
        if (t) fireMouse("mouseup", t.clientX, t.clientY);
      }
      if (e.touches.length === 0) {
        mode = "none";
        lastPinchDist = 0;
      } else if (e.touches.length === 1) {
        // pinch released a finger; resume pan with the remaining touch
        const t = e.touches[0];
        mode = "pan";
        lastPinchDist = 0;
        fireMouse("mousedown", t.clientX, t.clientY);
      }
    };

    canvas.addEventListener("touchstart", onStart, { passive: false });
    canvas.addEventListener("touchmove", onMove, { passive: false });
    canvas.addEventListener("touchend", onEnd);
    canvas.addEventListener("touchcancel", onEnd);

    return () => {
      canvas.removeEventListener("touchstart", onStart);
      canvas.removeEventListener("touchmove", onMove);
      canvas.removeEventListener("touchend", onEnd);
      canvas.removeEventListener("touchcancel", onEnd);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function WaterfallCanvas({
  config,
  onFrame,
  dbfsFloor = DBFS_MIN,
  dbfsCeiling = 0,
}: WaterfallCanvasProps) {
  const handle = useRef<WaterfallCanvasHandle>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const configRef = useRef<ViewerStreamConfigMessage | null>(null);

  useEffect(() => {
    configRef.current = config;
  }, [config]);

  useEffect(() => {
    const unsub = onFrame((frame: ViewerSpectrumFrameMessage) => {
      const cfg = configRef.current;
      if (!cfg || !handle.current) return;
      if (frame.config_version !== cfg.config_version) return;
      const parsed = toWaterfallFrame(frame, cfg);
      if (parsed) handle.current.push(parsed);
    });
    return unsub;
  }, [onFrame]);

  // Re-attach touch handlers whenever the library remounts its canvas (new rendererKey).
  const rendererKey = config
    ? `${config.agent_id}:${config.stream_id}:${config.session_id}:${config.config_version}`
    : null;
  useTouchBridge(containerRef, [rendererKey]);

  if (!config) {
    return (
      <div className="w-full h-[400px] bg-black flex items-center justify-center">
        <span className="text-gray-600 text-sm">Waiting for stream config…</span>
      </div>
    );
  }

  // Map the dBFS window [dbfsFloor, dbfsCeiling] to normalized sensitivity [0,1].
  const sensitivityLow = (dbfsFloor - DBFS_MIN) / DBFS_RANGE;
  const sensitivityHigh = (dbfsCeiling - DBFS_MIN) / DBFS_RANGE;

  return (
    <div ref={containerRef} className="w-full">
      <WaterfallLib
        key={rendererKey!}
        ref={handle}
        colorMap={interpolateTurbo}
        heightPx={400}
        rowHeight={2}
        tooltip
        timeBar
        freqFormat={freqFormat}
        valueFormat={valueFormat}
        sensitivity={{ low: sensitivityLow, high: sensitivityHigh }}
      />
    </div>
  );
}
