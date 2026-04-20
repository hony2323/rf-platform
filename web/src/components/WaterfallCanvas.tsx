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
import { toWaterfallFrame, freqFormat, valueFormat } from "../utils/fft";

interface WaterfallCanvasProps {
  config: ViewerStreamConfigMessage | null;
  onFrame: (cb: (frame: ViewerSpectrumFrameMessage) => void) => () => void;
}

export function WaterfallCanvas({ config, onFrame }: WaterfallCanvasProps) {
  const handle = useRef<WaterfallCanvasHandle>(null);
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

  if (!config) {
    return (
      <div className="w-full h-[400px] bg-black flex items-center justify-center">
        <span className="text-gray-600 text-sm">Waiting for stream config…</span>
      </div>
    );
  }

  const rendererKey = `${config.agent_id}:${config.stream_id}:${config.session_id}:${config.config_version}`;

  return (
    <WaterfallLib
      key={rendererKey}
      ref={handle}
      colorMap={interpolateTurbo}
      heightPx={400}
      rowHeight={2}
      tooltip
      timeBar
      freqFormat={freqFormat}
      valueFormat={valueFormat}
    />
  );
}
