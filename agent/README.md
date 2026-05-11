# rf-agent

SDR agent for live RF spectrum streaming. Reads IQ samples from an SDR device
(or a file/simulator), runs FFT, and streams spectrum frames over WebSocket
to an rf-platform server.

## Quick Start

```
pip install "rf-agent[sdr]"
rf-agent setup            # platform-specific OS prep (udev/libusb/Zadig)
rf-agent doctor           # verify everything is wired up
rf-agent connect --source rtl-sdr --freq 100000000 \
                 --server ws://your-server/ws/agent --token <token>
```

`rf-agent setup` auto-detects your platform and does the parts `pip install`
can't do. `rf-agent doctor` runs read-only checks and tells you exactly what's
wrong if `connect` doesn't work. See `rf-agent connect --help` for all flags.

## Per-platform notes

The `[sdr]` extra installs `pyrtlsdrlib`, which bundles `librtlsdr` binaries
for Linux/Windows/macOS. The only system dependency you still need is
**libusb**, plus a way for non-root user-space to access the USB device.
`rf-agent setup` handles both.

### Linux (Debian / Ubuntu / Fedora / Arch / openSUSE)

```
pip install "rf-agent[sdr]"
rf-agent setup linux      # prompts sudo to install libusb + udev rules
# log out + back in (or `newgrp plugdev`) so plugdev membership takes effect
rf-agent doctor
```

What `setup linux` actually does (idempotent):

1. Installs `libusb-1.0-0` (or the equivalent for dnf/pacman/zypper) if missing.
2. Writes `/etc/udev/rules.d/20-rtl-sdr.rules` with `MODE="0666", GROUP="plugdev"`.
3. Reloads udevadm rules.
4. Adds the current user to `plugdev`.

### Windows (native, no WSL)

```
pip install "rf-agent[sdr]"
rf-agent setup windows    # downloads & launches Zadig
# in Zadig: Options → List All Devices, pick 'Bulk-In, Interface (Interface 0)',
# select WinUSB, click Install Driver.
rf-agent doctor
```

The Zadig step is a Windows-GUI driver replacement — there's no fully
automated path because Windows requires user consent for driver swaps. After
`setup windows` opens Zadig, follow the on-screen instructions printed to the
console.

### macOS

```
pip install "rf-agent[sdr]"
rf-agent setup macos      # brew install libusb
rf-agent doctor
```

### WSL (Ubuntu under Windows)

```
# (one-time, from an ELEVATED Windows PowerShell)
usbipd bind --busid <BUSID-of-your-dongle>

# inside WSL:
pip install "rf-agent[sdr]"
rf-agent setup linux       # udev rules + libusb, same as Linux
rf-agent setup wsl-attach  # attaches the dongle via usbipd; rerun after every unplug
rf-agent doctor
```

## What `pip install` fundamentally cannot do

These steps require admin/root and live outside the Python packaging system.
`rf-agent setup` automates them where it can, but the underlying constraint
is real:

- **Linux**: writing `/etc/udev/rules.d/*.rules` and `usermod -aG plugdev` need root.
- **Windows**: replacing the Realtek driver with WinUSB needs admin consent (GUI).
- **WSL**: `usbipd bind` is a one-time elevated PowerShell command on the
  Windows host.
- **macOS**: `brew install libusb` if not already present.

## Troubleshooting

### `rf-agent doctor` says everything is OK but `connect` shows only noise

That's an RF problem, not a software problem. Try:

- Increase gain: `--rtlsdr-gain 49.6`.
- Tune slightly off-center to dodge the DC/LO spike: e.g. `--freq 99500000`
  instead of `100000000`.
- Use a longer antenna for FM (each element ~75 cm).
- Confirm a known-strong local station is within ±1 MHz of your `--freq`.

### `usb_claim_interface error -6` / "Resource busy"

Another process holds the dongle — usually `gqrx`, a previous `rf-agent` run,
or the kernel's DVB driver auto-binding on hotplug. Quick recovery:

```
sudo rmmod dvb_usb_rtl28xxu rtl2832 rtl2830 2>/dev/null  # Linux
pkill -f gqrx ; pkill -f rtl_tcp
# or just: unplug + replug
```

On WSL, after a replug you also need to re-attach: `rf-agent setup wsl-attach`.

## How it works (appendix)

### librtlsdr symbol-stub workaround

`pyrtlsdr 0.4.0` resolves several optional librtlsdr symbols at *import time*
without `try/except`: `rtlsdr_set_dithering` and the GPIO helpers
(`rtlsdr_set_gpio_input/output/bit/byte/status`, `rtlsdr_get_gpio_bit/byte`).
The system `librtlsdr` shipped by Debian/Ubuntu (`2.0.2`) doesn't export them
— and neither do the upstream osmocom or rtl-sdr-blog source trees — so
`import rtlsdr` raises `AttributeError` on a stock Linux install.

When `pyrtlsdrlib` is present (which it is via the `[sdr]` extra), pyrtlsdr
loads from the bundled binary which *does* export all 8 symbols, so this
never trips.

As defence-in-depth (users on systems with a partial librtlsdr but no
pyrtlsdrlib), `agent/source/rtl_sdr_source.py::_install_missing_symbol_stubs`
monkey-patches `ctypes.CDLL.__getattr__` to return no-op stubs for the missing
symbols. It runs once on the first `RTLSDRSource.start()` call. The agent
never *calls* these functions (GPIO + dithering control we don't need), so
the stubs are inert.

### USB throttling and backpressure

`RTLSDRSource` sleeps `1/fps` between reads. The hardware buffer overruns —
that's expected, we just keep the latest snapshot. Default `--fps 10` means
~10 FFT frames/sec when `--rtlsdr-chunk-samples` is left at its default
(equal to `--fft-size`, i.e. one chunk = one frame).

`--fps` is currently capped at **10** on the free tier. Premium-tier accounts
may exceed this in the future; the cap is enforced at the CLI entry point
(`agent/cli.py::MAX_FPS_FREE_TIER`).

The source emits to its output queue with **latest-frame-wins** semantics: if
the consumer can't keep up and the queue is full, the oldest pending frame
is dropped before the new one is enqueued. This prevents memory growth under
slow consumers and ensures the hardware loop never blocks. Drops are visible
via `RTLSDRSource.frames_dropped`.
