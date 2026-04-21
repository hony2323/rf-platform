import {
  WaterfallRenderer
} from "./chunk-CQ5TCDRY.js";

// src/WaterfallCanvas.tsx
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { jsx } from "react/jsx-runtime";
var WaterfallCanvas = forwardRef(
  function WaterfallCanvas2({ rowCount = 400, heightPx = 400, rowHeight = 1, bufferWidth, minSpan, colorMap, tooltip, timeBar, timeBarDynamic, freqFormat, valueFormat, lazyThreshold, direction, flipFreq, smoothPixels, smoothZoom, sensitivity, gamma, onMetrics }, ref) {
    const canvasRef = useRef(null);
    const rendererRef = useRef(null);
    const onMetricsRef = useRef(onMetrics);
    onMetricsRef.current = onMetrics;
    useImperativeHandle(ref, () => ({
      push: (frame) => rendererRef.current?.push(frame),
      exportImage: (options) => rendererRef.current?.exportImage(options)
    }), []);
    useEffect(() => {
      const renderer = new WaterfallRenderer(canvasRef.current, { rowCount, bufferWidth, minSpan, colorMap, tooltip, timeBar, timeBarDynamic, freqFormat, valueFormat, lazyThreshold, direction, flipFreq, smoothPixels, smoothZoom, sensitivity, gamma });
      renderer.onMetrics = (...args) => onMetricsRef.current?.(...args);
      rendererRef.current = renderer;
      return () => {
        renderer.destroy();
        rendererRef.current = null;
      };
    }, [rowCount, bufferWidth, minSpan, colorMap, tooltip, timeBar, timeBarDynamic, freqFormat, valueFormat, lazyThreshold, direction, flipFreq, smoothPixels, smoothZoom]);
    useEffect(() => {
      if (rendererRef.current) rendererRef.current.rowHeight = rowHeight;
    }, [rowHeight]);
    useEffect(() => {
      if (rendererRef.current && sensitivity) rendererRef.current.sensitivity = sensitivity;
    }, [sensitivity?.low, sensitivity?.high]);
    useEffect(() => {
      if (rendererRef.current && gamma !== void 0) rendererRef.current.gamma = gamma;
    }, [gamma]);
    return /* @__PURE__ */ jsx(
      "canvas",
      {
        ref: canvasRef,
        style: { width: "100%", height: `${heightPx}px`, display: "block" }
      }
    );
  }
);
export {
  WaterfallCanvas
};
//# sourceMappingURL=react.js.map