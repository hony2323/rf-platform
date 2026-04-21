import * as react from 'react';
import { P as ParsedFrame, E as ExportImageOptions } from './WaterfallRenderer-D8lyjGc_.cjs';

interface WaterfallCanvasHandle {
    push(frame: ParsedFrame): void;
    exportImage(options?: ExportImageOptions): void;
}
interface WaterfallCanvasProps {
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
    direction?: 'top' | 'bottom' | 'left' | 'right';
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
declare const WaterfallCanvas: react.ForwardRefExoticComponent<WaterfallCanvasProps & react.RefAttributes<WaterfallCanvasHandle>>;

export { WaterfallCanvas, type WaterfallCanvasHandle, type WaterfallCanvasProps };
