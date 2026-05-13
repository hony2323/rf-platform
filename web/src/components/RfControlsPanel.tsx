import { useEffect, useState } from "react";
import type {
  RfConfig,
  RfConfigRequest,
  TunerConfigRequest,
} from "../types/viewer";
import { RequestConfigError } from "../hooks/useViewerStream";

const VALID_SAMPLE_RATES_HZ = [
  240_000, 1_024_000, 1_600_000, 2_400_000, 2_560_000,
];

const VALID_FFT_SIZES = [
  1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072,
];

interface RfControlsPanelProps {
  config: RfConfig;
  enabled: boolean;
  sendRequestConfig: (
    rf: RfConfigRequest,
    tuner: TunerConfigRequest | null,
  ) => Promise<void>;
}

type SubmitStatus =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "ok"; message: string }
  | { kind: "error"; message: string };

export function RfControlsPanel({
  config,
  enabled,
  sendRequestConfig,
}: RfControlsPanelProps) {
  const [open, setOpen] = useState(false);
  const [centerFreqMhz, setCenterFreqMhz] = useState(
    config.center_freq_hz / 1_000_000,
  );
  const [sampleRateHz, setSampleRateHz] = useState(config.sample_rate_hz);
  const [fftSize, setFftSize] = useState(config.fft_size);
  const [windowFn, setWindowFn] = useState(config.window_fn || "hann");
  const [agc, setAgc] = useState(true);
  const [gainDb, setGainDb] = useState(20);
  const [status, setStatus] = useState<SubmitStatus>({ kind: "idle" });

  // Re-sync form when the server-confirmed config changes (e.g. after a
  // successful submit, or when another viewer changed it).
  useEffect(() => {
    setCenterFreqMhz(config.center_freq_hz / 1_000_000);
    setSampleRateHz(config.sample_rate_hz);
    setFftSize(config.fft_size);
    setWindowFn(config.window_fn || "hann");
  }, [
    config.center_freq_hz,
    config.sample_rate_hz,
    config.fft_size,
    config.window_fn,
  ]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!enabled || status.kind === "pending") return;
    setStatus({ kind: "pending" });
    try {
      await sendRequestConfig(
        {
          center_freq_hz: Math.round(centerFreqMhz * 1_000_000),
          sample_rate_hz: sampleRateHz,
          fft_size: fftSize,
          window_fn: windowFn,
        },
        { gain_db: agc ? null : gainDb, agc },
      );
      setStatus({ kind: "ok", message: "Applied" });
    } catch (err) {
      const code =
        err instanceof RequestConfigError ? err.code : "ERROR";
      const message =
        err instanceof Error ? err.message : String(err);
      setStatus({ kind: "error", message: `${code}: ${message}` });
    }
  };

  const formDisabled = !enabled || status.kind === "pending";

  return (
    <div className="border-b border-white/10 bg-white/[0.02] px-3 py-2 text-xs sm:px-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left text-gray-400 hover:text-white"
      >
        <span className="font-medium">RF controls</span>
        <span className="text-gray-600">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <form
          onSubmit={handleSubmit}
          className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3"
        >
          <label className="flex flex-col gap-1">
            <span className="text-gray-500">Center freq (MHz)</span>
            <input
              type="number"
              step="0.001"
              min="1"
              max="2700"
              value={centerFreqMhz}
              onChange={(e) => setCenterFreqMhz(Number(e.target.value))}
              disabled={formDisabled}
              className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-200 focus:border-blue-500 focus:outline-none disabled:opacity-50"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-500">Sample rate</span>
            <select
              value={sampleRateHz}
              onChange={(e) => setSampleRateHz(Number(e.target.value))}
              disabled={formDisabled}
              className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-200 focus:border-blue-500 focus:outline-none disabled:opacity-50"
            >
              {VALID_SAMPLE_RATES_HZ.map((r) => (
                <option key={r} value={r}>
                  {(r / 1_000_000).toFixed(3)} Msps
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-500">FFT size</span>
            <select
              value={fftSize}
              onChange={(e) => setFftSize(Number(e.target.value))}
              disabled={formDisabled}
              className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-200 focus:border-blue-500 focus:outline-none disabled:opacity-50"
            >
              {VALID_FFT_SIZES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-500">Window</span>
            <select
              value={windowFn}
              onChange={(e) => setWindowFn(e.target.value)}
              disabled={formDisabled}
              className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-200 focus:border-blue-500 focus:outline-none disabled:opacity-50"
            >
              <option value="hann">hann</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 sm:col-span-2">
            <span className="flex items-center justify-between text-gray-500">
              <span>Gain {agc ? "(AGC)" : `${gainDb.toFixed(1)} dB`}</span>
              <label className="flex items-center gap-1 text-gray-400">
                <input
                  type="checkbox"
                  checked={agc}
                  onChange={(e) => setAgc(e.target.checked)}
                  disabled={formDisabled}
                  className="rounded border-gray-700 bg-gray-800"
                />
                <span>AGC</span>
              </label>
            </span>
            <input
              type="range"
              min="0"
              max="49.6"
              step="0.1"
              value={gainDb}
              onChange={(e) => setGainDb(Number(e.target.value))}
              disabled={formDisabled || agc}
              className="w-full disabled:opacity-50"
            />
          </label>
          <div className="flex items-end justify-between gap-2 sm:col-span-2 lg:col-span-3">
            <span
              className={
                status.kind === "error"
                  ? "text-red-400"
                  : status.kind === "ok"
                  ? "text-green-400"
                  : "text-gray-500"
              }
            >
              {status.kind === "pending"
                ? "Applying…"
                : status.kind === "ok"
                ? status.message
                : status.kind === "error"
                ? status.message
                : ""}
            </span>
            <button
              type="submit"
              disabled={formDisabled}
              className="rounded bg-blue-600 px-3 py-1 font-medium text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Apply
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
