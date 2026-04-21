declare module "@hony2323/waterfall-canvas/react" {
  import type * as React from "react";
  import type { ParsedFrame } from "@hony2323/waterfall-canvas";

  export interface WaterfallCanvasHandle {
    push(frame: ParsedFrame): void;
    exportImage(options?: unknown): void;
  }

  export interface WaterfallCanvasProps {
    rowCount?: number;
    heightPx?: number;
    rowHeight?: number;
    bufferWidth?: number;
    minSpan?: number;
    colorMap?: (t: number) => [number, number, number];
    tooltip?: boolean;
    timeBar?: boolean;
    timeBarDynamic?: boolean;
    freqFormat?: (hz: number) => string;
    valueFormat?: (t: number) => string;
    lazyThreshold?: number;
    direction?: "top" | "bottom" | "left" | "right";
    flipFreq?: boolean;
    smoothPixels?: boolean;
    smoothZoom?: boolean;
    sensitivity?: {
      low: number;
      high: number;
    };
    gamma?: number;
    onMetrics?: (pushMs: number, renderMs: number, isLazy: boolean) => void;
  }

  export const WaterfallCanvas: React.ForwardRefExoticComponent<
    WaterfallCanvasProps & React.RefAttributes<WaterfallCanvasHandle>
  >;
}
