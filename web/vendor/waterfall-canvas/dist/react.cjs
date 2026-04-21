"use strict";Object.defineProperty(exports, "__esModule", {value: true}); function _optionalChain(ops) { let lastAccessLHS = undefined; let value = ops[0]; let i = 1; while (i < ops.length) { const op = ops[i]; const fn = ops[i + 1]; i += 2; if ((op === 'optionalAccess' || op === 'optionalCall') && value == null) { return undefined; } if (op === 'access' || op === 'optionalAccess') { lastAccessLHS = value; value = fn(value); } else if (op === 'call' || op === 'optionalCall') { value = fn((...args) => value.call(lastAccessLHS, ...args)); lastAccessLHS = undefined; } } return value; }

var _chunkYGG7ODPQcjs = require('./chunk-YGG7ODPQ.cjs');

// src/WaterfallCanvas.tsx
var _react = require('react');
var _jsxruntime = require('react/jsx-runtime');
var WaterfallCanvas = _react.forwardRef.call(void 0, 
  function WaterfallCanvas2({ rowCount = 400, heightPx = 400, rowHeight = 1, bufferWidth, minSpan, colorMap, tooltip, timeBar, timeBarDynamic, freqFormat, valueFormat, lazyThreshold, direction, flipFreq, smoothPixels, smoothZoom, sensitivity, gamma, onMetrics }, ref) {
    const canvasRef = _react.useRef.call(void 0, null);
    const rendererRef = _react.useRef.call(void 0, null);
    const onMetricsRef = _react.useRef.call(void 0, onMetrics);
    onMetricsRef.current = onMetrics;
    _react.useImperativeHandle.call(void 0, ref, () => ({
      push: (frame) => _optionalChain([rendererRef, 'access', _ => _.current, 'optionalAccess', _2 => _2.push, 'call', _3 => _3(frame)]),
      exportImage: (options) => _optionalChain([rendererRef, 'access', _4 => _4.current, 'optionalAccess', _5 => _5.exportImage, 'call', _6 => _6(options)])
    }), []);
    _react.useEffect.call(void 0, () => {
      const renderer = new (0, _chunkYGG7ODPQcjs.WaterfallRenderer)(canvasRef.current, { rowCount, bufferWidth, minSpan, colorMap, tooltip, timeBar, timeBarDynamic, freqFormat, valueFormat, lazyThreshold, direction, flipFreq, smoothPixels, smoothZoom, sensitivity, gamma });
      renderer.onMetrics = (...args) => _optionalChain([onMetricsRef, 'access', _7 => _7.current, 'optionalCall', _8 => _8(...args)]);
      rendererRef.current = renderer;
      return () => {
        renderer.destroy();
        rendererRef.current = null;
      };
    }, [rowCount, bufferWidth, minSpan, colorMap, tooltip, timeBar, timeBarDynamic, freqFormat, valueFormat, lazyThreshold, direction, flipFreq, smoothPixels, smoothZoom]);
    _react.useEffect.call(void 0, () => {
      if (rendererRef.current) rendererRef.current.rowHeight = rowHeight;
    }, [rowHeight]);
    _react.useEffect.call(void 0, () => {
      if (rendererRef.current && sensitivity) rendererRef.current.sensitivity = sensitivity;
    }, [_optionalChain([sensitivity, 'optionalAccess', _9 => _9.low]), _optionalChain([sensitivity, 'optionalAccess', _10 => _10.high])]);
    _react.useEffect.call(void 0, () => {
      if (rendererRef.current && gamma !== void 0) rendererRef.current.gamma = gamma;
    }, [gamma]);
    return /* @__PURE__ */ _jsxruntime.jsx.call(void 0, 
      "canvas",
      {
        ref: canvasRef,
        style: { width: "100%", height: `${heightPx}px`, display: "block" }
      }
    );
  }
);


exports.WaterfallCanvas = WaterfallCanvas;
//# sourceMappingURL=react.cjs.map