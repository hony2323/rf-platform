# rf-agent

SDR agent for live RF spectrum streaming. Reads IQ samples from an SDR device (or a file/simulator), runs FFT, and streams spectrum frames over WebSocket to an rf-platform server.

## Install

```
pip install rf-agent
```

With RTL-SDR hardware support:

```
pip install "rf-agent[sdr]"
```

## Usage

```
rf-agent connect --server ws://your-server/ws/agent --token <token>
```

See `rf-agent connect --help` for all options.

## Troubleshooting

### `undefined symbol: rtlsdr_set_dithering` (and friends)

`pyrtlsdr 0.4.0` resolves several optional librtlsdr symbols at *import time* —
`rtlsdr_set_dithering`, `rtlsdr_set_gpio_input/output/bit/byte/status`,
`rtlsdr_get_gpio_bit/byte`. None are wrapped in try/except, so if the system's
`librtlsdr` doesn't export them, `import rtlsdr` raises `AttributeError` and the
agent fails to start.

The `librtlsdr0` package shipped by Debian/Ubuntu (`2.0.2`) is missing these
symbols entirely — they aren't in the upstream osmocom or rtl-sdr-blog source
trees either, so rebuilding from source doesn't help.

**Fix (already in the codebase):** before importing `rtlsdr` we monkey-patch
`ctypes.CDLL.__getattr__` to return a no-op stub for any symbol in a known
"missing" set. The agent never *calls* these functions (they're for GPIO and
ADC dithering control we don't need), so the stubs are inert. The patch lives
in `agent/source/rtl_sdr_source.py::_install_missing_symbol_stubs` and runs
once on the first call to `RTLSDRSource.start()`. On systems whose librtlsdr
already exports the symbols the patch is a pass-through.

### `usb_claim_interface error -6` / `Resource busy`

Another process is holding the dongle (gqrx, a previous agent run, or the
kernel's DVB driver auto-binding). Quickest fix: unplug + replug. On WSL,
re-attach via `task usb:attach`.
