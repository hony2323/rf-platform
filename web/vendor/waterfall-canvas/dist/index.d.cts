export { B as BandHeader, P as ParsedFrame, W as WaterfallOptions, a as WaterfallRenderer } from './WaterfallRenderer-D8lyjGc_.cjs';

/** Grayscale: black → white */
declare function interpolateGrayscale(t: number): [number, number, number];
/** Hot colormap: black → red → yellow → white */
declare function interpolateHot(t: number): [number, number, number];
/**
 * Turbo colormap (Google, 2019) — perceptually-uniform rainbow.
 * Polynomial coefficients from d3-scale-chromatic / Google AI Blog.
 */
declare function interpolateTurbo(t: number): [number, number, number];
/** Build a 256-entry packed RGB LUT from any colormap function. */
declare function buildLut(colorMap: (t: number) => [number, number, number]): Uint8Array;
/**
 * Normalize a raw sample value to [0, 1] based on wire precision.
 * uint8   — full 0–255 range.
 * uint16  — full 0–65535 range.
 * float32 — backend sends values in the 0–100 range.
 */
declare function normalizeValue(value: number, precision: string): number;

export { buildLut, interpolateGrayscale, interpolateHot, interpolateTurbo, normalizeValue };
