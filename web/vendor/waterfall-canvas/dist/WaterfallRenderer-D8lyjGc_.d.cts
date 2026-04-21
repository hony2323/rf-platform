interface BandHeader {
    band_id: string;
    band_start: number;
    band_end: number;
    timestamp: string;
    sent_at: number;
    length: number;
    precision: string;
}
interface ParsedFrame {
    header: BandHeader[];
    bands: Record<string, Uint8Array | Uint16Array | Float32Array>;
}

interface WaterfallOptions {
    /** Number of history rows in the ring buffer (also sets canvas pixel height). Default: 400 */
    rowCount?: number;
    /**
     * Colormap function: receives a normalized value t ∈ [0, 1] and returns [r, g, b] (0–255).
     * Defaults to grayscale. A 256-entry LUT is pre-computed at construction time.
     */
    colorMap?: (t: number) => [number, number, number];
    /**
     * Max width of the ring buffer in pixels. Input is downsampled to this width when
     * totalSamples exceeds it, keeping memory bounded.
     * Memory cost: bufferWidth × rowCount × 4 bytes.
     * Default: 4096 (~6 MB at rowCount=400). Set to 0 to use full input resolution.
     */
    bufferWidth?: number;
    /**
     * Minimum number of ring-buffer pixels visible (= maximum zoom level).
     * Default: 32. Lower = more zoom possible.
     */
    minSpan?: number;
    /**
     * Show a hover tooltip with band, frequency, time, and signal value.
     * Allocates an additional Float32Array (ringWidth × rowCount) for value storage.
     * Default: false.
     */
    tooltip?: boolean;
    /**
     * Format the frequency shown in the tooltip. Receives the raw Hz value.
     * Default: hz => hz.toFixed(1)
     */
    freqFormat?: (hz: number) => string;
    /**
     * Format the signal value shown in the tooltip. Receives normalized t ∈ [0, 1].
     * Default: t => (t * 100).toFixed(1) + '%'
     */
    valueFormat?: (t: number) => string;
    /**
     * Show a time axis on the left edge of the canvas with time-ago labels.
     * Reads from the same timeBuffer as the tooltip (allocated when either is true).
     * Default: false.
     */
    timeBar?: boolean;
    /**
     * When true, the time-ago labels in the time bar update live every rAF tick.
     * When false (default), labels only update when new data arrives — no jumping.
     */
    timeBarDynamic?: boolean;
    /**
     * Source-pixels-per-output-pixel ratio above which the per-pixel max-value scan
     * is skipped (center-pixel sampling instead). Kicks in only when significantly
     * zoomed out, where the scan is expensive and spikes are sub-pixel anyway.
     * Default: 4. Set to Infinity to always preserve spikes.
     */
    lazyThreshold?: number;
    /**
     * Direction the waterfall scrolls:
     * - `'top'` (default): newest row at top, time scrolls downward, frequency on x-axis
     * - `'bottom'`: newest row at bottom, time scrolls upward, frequency on x-axis
     * - `'left'`: newest column at left, time scrolls rightward, frequency on y-axis
     * - `'right'`: newest column at right, time scrolls leftward, frequency on y-axis
     */
    direction?: 'top' | 'bottom' | 'left' | 'right';
    /**
     * Flip the frequency axis. For horizontal directions this reverses the y-axis
     * (low frequencies at the bottom); for vertical directions it reverses the x-axis
     * (low frequencies on the right). Default: false.
     */
    flipFreq?: boolean;
    /**
     * Bilinear interpolation when mapping source pixels to output pixels.
     * Produces smooth gradients at the cost of spike preservation (max-value
     * pooling is bypassed). Default: false (nearest / max-value).
     */
    smoothPixels?: boolean;
    /**
     * Animate zoom transitions. When true, wheel events smoothly lerp the view
     * to the target zoom level each rAF tick instead of snapping instantly.
     * Default: false.
     */
    smoothZoom?: boolean;
    /**
     * Linear window applied after wire normalization.
     * Values below `low` map to 0; values above `high` map to 1.
     * Narrowing the window amplifies faint signals. Default: { low: 0, high: 1 } (no-op).
     */
    sensitivity?: {
        low: number;
        high: number;
    };
    /**
     * Gamma / power-curve applied after sensitivity.
     * < 1 pulls faint signals up (brighter); > 1 pushes them down (darker).
     * Default: 1 (no-op).
     */
    gamma?: number;
}
interface ExportImageOptions {
    /**
     * Image format.
     * - `'bmp'` — uncompressed, single file, no size limit (default)
     * - `'png'` — compressed, tiled into multiple files if width > 32,767px
     */
    format?: 'bmp' | 'png';
    /** Base filename without extension. Default: `'waterfall'` */
    filename?: string;
}
declare class WaterfallRenderer {
    /** Called from the rAF loop after each render. Assign freely — no re-render side effects. */
    onMetrics?: (pushMs: number, renderMs: number, isLazy: boolean) => void;
    /** Pixel height of each time-slice row. Higher = faster-looking waterfall. Default: 1 */
    rowHeight: number;
    private _sensitivity;
    private _gamma;
    get sensitivity(): {
        low: number;
        high: number;
    };
    set sensitivity(v: {
        low: number;
        high: number;
    });
    get gamma(): number;
    set gamma(v: number);
    private readonly canvas;
    private readonly rowCount;
    private readonly bufferWidth;
    private readonly lut;
    private readonly tooltipEnabled;
    private readonly timeBarEnabled;
    private readonly timeBarDynamic;
    private readonly minSpan;
    private readonly lazyThreshold;
    private readonly freqFormat;
    private readonly valueFormat;
    private readonly direction;
    private readonly isHorizontal;
    private readonly flipFreq;
    private readonly flipFreqActual;
    private readonly smoothPixels;
    private readonly smoothZoom;
    private targetStart;
    private targetEnd;
    private timeBarNow;
    private imgData;
    private viewImg;
    private ctx;
    private valueBuffer;
    private timeBuffer;
    private tooltipEl;
    private headRow;
    private dirty;
    private viewDirty;
    private viewStart;
    private viewEnd;
    private ringWidth;
    private totalSamples;
    private bandRanges;
    private initialized;
    private rafId;
    private pendingPushMs;
    private dragActive;
    private lastDragPos;
    private lastMouseEvent;
    private readonly ro;
    private readonly _boundLoop;
    private readonly _boundWheel;
    private readonly _boundMouseDown;
    private readonly _boundMouseMove;
    private readonly _boundMouseUp;
    constructor(canvas: HTMLCanvasElement, options?: WaterfallOptions);
    push(frame: ParsedFrame): void;
    /**
     * Download the full ring buffer as an image file.
     * BMP is uncompressed and has no size limit; PNG is tiled when width > 32,767px.
     */
    exportImage(options?: ExportImageOptions): void;
    destroy(): void;
    private _resizeCanvas;
    private _bandSampleCount;
    private _init;
    private _pushRow;
    /** Render for 'top' and 'bottom' directions (frequency on x-axis, time on y-axis). */
    private _renderViewport;
    /** Render for 'left' and 'right' directions (frequency on y-axis, time on x-axis). */
    private _renderViewportHorizontal;
    private _drawTimeBar;
    private _loop;
    private _updateTooltip;
    private _encodeBmp;
    private _triggerDownload;
    private _onWheel;
    private _onMouseDown;
    private _onMouseMove;
    private _onMouseUp;
}

export { type BandHeader as B, type ExportImageOptions as E, type ParsedFrame as P, type WaterfallOptions as W, WaterfallRenderer as a };
