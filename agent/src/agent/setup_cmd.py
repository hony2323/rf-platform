"""rf-agent setup — install the OS-level prerequisites for RTL-SDR.

These are the steps `pip install` can't do (write udev rules, replace USB
drivers, brew install libusb). The Python implementation lives here so end
users who only ran ``pip install rf-agent[sdr]`` can run ``rf-agent setup``
without cloning the repo.

Each platform helper invokes ``sudo`` (or admin equivalents) for the
specifically-privileged steps and otherwise runs as the calling user.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Bundled content
# ---------------------------------------------------------------------------


# udev rule lines are intentionally long because that's the literal file format
# udev expects. We assemble UDEV_RULES_TEXT from a list to keep line length
# under the ruff E501 limit while preserving the on-disk content.
_RTLSDR_VENDOR_PRODUCT_IDS = ("0bda:2832", "0bda:2838", "0bda:2831")


def _udev_rule_line(vid_pid: str) -> str:
    vid, pid = vid_pid.split(":")
    return (
        f'SUBSYSTEM=="usb", ATTRS{{idVendor}}=="{vid}", '
        f'ATTRS{{idProduct}}=="{pid}", GROUP="plugdev", MODE="0666", '
        f'SYMLINK+="rtl_sdr"'
    )


UDEV_RULES_TEXT = (
    "# udev rules for RTL-SDR dongles (Realtek RTL2832U).\n"
    "# Installed by `rf-agent setup linux`.\n"
    "# Grants USB access to the `plugdev` group so non-root users can run the agent.\n"
    "\n" + "\n".join(_udev_rule_line(v) for v in _RTLSDR_VENDOR_PRODUCT_IDS) + "\n"
)

UDEV_RULES_PATH = "/etc/udev/rules.d/20-rtl-sdr.rules"

# Zadig is published under the libwdi repo with `b###` build tags. The asset
# name changes per release (`zadig-2.7.exe`, `zadig-2.9.exe`, ...), so resolve
# it dynamically from the GitHub API instead of hard-coding a stale URL.
_ZADIG_RELEASES_API = "https://api.github.com/repos/pbatard/libwdi/releases?per_page=10"
_ZADIG_FALLBACK_HOMEPAGE = "https://zadig.akeo.ie"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, label: str | None = None) -> int:
    """Run a subprocess and return its exit code.

    Never raises: missing binaries, OS errors, and non-zero exit codes are all
    reported as actionable one-line messages to stderr. Callers check the
    returned code and bail early on non-zero.
    """
    print(f"  $ {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, text=True)
    except FileNotFoundError:
        print(f"Error: command not found: {cmd[0]}", file=sys.stderr)
        return 127
    except OSError as e:
        print(f"Error running {cmd[0]}: {e}", file=sys.stderr)
        return 1
    if r.returncode != 0:
        print(
            f"Error: {label or cmd[0]} exited with code {r.returncode}",
            file=sys.stderr,
        )
    return r.returncode


def _has_apt() -> bool:
    return shutil.which("apt-get") is not None


def _has_dnf() -> bool:
    return shutil.which("dnf") is not None


def _has_pacman() -> bool:
    return shutil.which("pacman") is not None


def _has_zypper() -> bool:
    return shutil.which("zypper") is not None


def _ldconfig_has(libname: str) -> bool:
    if shutil.which("ldconfig") is None:
        return False
    try:
        out = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, check=False
        ).stdout
    except OSError:
        return False
    return libname in out


# ---------------------------------------------------------------------------
# Linux / WSL
# ---------------------------------------------------------------------------


def setup_linux() -> int:
    """Install udev rules, ensure libusb-1.0, add user to plugdev.

    Returns a non-zero exit code on failure.
    """
    sudo = [] if os.geteuid() == 0 else ["sudo"]
    if sudo and shutil.which("sudo") is None:
        print(
            "Error: needs root or sudo, but `sudo` is not installed.", file=sys.stderr
        )
        return 1

    # 1. libusb
    if _ldconfig_has("libusb-1.0.so.0"):
        print("[libusb] already installed.")
    else:
        print("[libusb] installing...")
        if _has_apt():
            rc = _run([*sudo, "apt-get", "update", "-y"], label="apt-get update")
            if rc != 0:
                return rc
            rc = _run(
                [*sudo, "apt-get", "install", "-y", "libusb-1.0-0"],
                label="apt-get install libusb-1.0-0",
            )
        elif _has_dnf():
            rc = _run(
                [*sudo, "dnf", "install", "-y", "libusbx"], label="dnf install libusbx"
            )
        elif _has_pacman():
            rc = _run(
                [*sudo, "pacman", "-S", "--noconfirm", "libusb"],
                label="pacman -S libusb",
            )
        elif _has_zypper():
            rc = _run(
                [*sudo, "zypper", "install", "-y", "libusb-1_0-0"],
                label="zypper install libusb-1_0-0",
            )
        else:
            print(
                "Error: no supported package manager (apt/dnf/pacman/zypper) "
                "found on this system.\n"
                "Install libusb-1.0 manually for your distro, then re-run:\n"
                "  rf-agent setup linux",
                file=sys.stderr,
            )
            return 1
        if rc != 0:
            return rc

    # 2. udev rules
    existing = Path(UDEV_RULES_PATH)
    try:
        already_installed = (
            existing.exists() and existing.read_text() == UDEV_RULES_TEXT
        )
    except OSError:
        already_installed = False
    if already_installed:
        print(f"[udev] rules already installed at {UDEV_RULES_PATH}.")
    else:
        print(f"[udev] installing rules to {UDEV_RULES_PATH}...")
        tmp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".rules", delete=False
            ) as tmp:
                tmp.write(UDEV_RULES_TEXT)
                tmp_path = tmp.name
            rc = _run(
                [*sudo, "install", "-m", "0644", tmp_path, UDEV_RULES_PATH],
                label="install udev rules",
            )
            if rc != 0:
                return rc
            rc = _run(
                [*sudo, "udevadm", "control", "--reload-rules"],
                label="udevadm control --reload-rules",
            )
            if rc != 0:
                return rc
            rc = _run([*sudo, "udevadm", "trigger"], label="udevadm trigger")
            if rc != 0:
                return rc
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    # File may have been moved by `install`, or otherwise gone;
                    # nothing left to clean up.
                    pass

    # 3. plugdev membership
    user = (
        os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or os.environ.get("LOGNAME")
        or ""
    )
    if not user:
        print(
            "[plugdev] could not determine current user; skipping group add.",
            file=sys.stderr,
        )
    else:
        try:
            import grp

            members = grp.getgrnam("plugdev").gr_mem
        except KeyError:
            print("[plugdev] group missing; creating it.")
            rc = _run([*sudo, "groupadd", "plugdev"], label="groupadd plugdev")
            if rc != 0:
                return rc
            members = []
        if user in members:
            print(f"[plugdev] user '{user}' is already a member.")
        else:
            print(f"[plugdev] adding '{user}' to plugdev...")
            rc = _run(
                [*sudo, "usermod", "-aG", "plugdev", user],
                label=f"usermod -aG plugdev {user}",
            )
            if rc != 0:
                return rc
            print(
                "  -> log out and back in (or run `newgrp plugdev`) "
                "for this to take effect."
            )

    print()
    print(
        "Done. Plug in your RTL-SDR (or unplug+replug if already connected), "
        "then verify with:"
    )
    print("  rf-agent doctor")
    return 0


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def setup_macos() -> int:
    if shutil.which("brew") is None:
        print(
            "Error: Homebrew is required. Install from https://brew.sh then re-run.",
            file=sys.stderr,
        )
        return 1

    # `brew list libusb` exits 0 if installed, non-zero otherwise.
    r = subprocess.run(
        ["brew", "list", "libusb"],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode == 0:
        print("[libusb] already installed.")
    else:
        print("[libusb] installing via Homebrew...")
        rc = _run(["brew", "install", "libusb"], label="brew install libusb")
        if rc != 0:
            return rc

    print()
    print("Done. Plug in your RTL-SDR and run:")
    print("  rf-agent doctor")
    return 0


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def _fetch_latest_zadig_url() -> str | None:
    """Find the most recent ``zadig-*.exe`` asset in the libwdi releases.

    Returns ``None`` if the API is unreachable or no matching asset is found.
    """
    import json
    import urllib.request

    req = urllib.request.Request(
        _ZADIG_RELEASES_API,
        headers={"Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            releases = json.load(resp)
    except (OSError, json.JSONDecodeError):
        return None
    for rel in releases:
        for asset in rel.get("assets") or []:
            name = asset.get("name", "")
            if name.startswith("zadig-") and name.endswith(".exe"):
                url = asset.get("browser_download_url")
                if isinstance(url, str):
                    return url
    return None


def setup_windows() -> int:
    """Download Zadig (if not cached) and launch it for driver replacement."""
    if sys.platform != "win32":
        print(
            "Error: `rf-agent setup windows` only works on native Windows. "
            "On WSL, use `rf-agent setup linux` plus `usbipd attach`.",
            file=sys.stderr,
        )
        return 1

    import urllib.request

    cache_dir = (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "rf-agent" / "zadig"
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    zadig_path = cache_dir / "zadig.exe"

    if zadig_path.exists():
        print(f"[zadig] cached at {zadig_path}")
    else:
        print("[zadig] resolving latest release via GitHub API ...")
        zadig_url = _fetch_latest_zadig_url()
        if zadig_url is None:
            print(
                f"Error: could not resolve a Zadig download URL.\n"
                f"Download it manually from {_ZADIG_FALLBACK_HOMEPAGE} and "
                f"re-run after placing zadig.exe at:\n  {zadig_path}",
                file=sys.stderr,
            )
            return 1
        print(f"[zadig] downloading {zadig_url} ...")
        try:
            urllib.request.urlretrieve(zadig_url, zadig_path)
        except OSError as e:
            print(
                f"Error: download failed ({e}). "
                f"Visit {_ZADIG_FALLBACK_HOMEPAGE} manually.",
                file=sys.stderr,
            )
            return 1

    print(
        """
====================================================================
Zadig is about to open. In the Zadig window:

  1. Menu: Options → 'List All Devices'.
  2. Top dropdown: select 'Bulk-In, Interface (Interface 0)'
     (sometimes 'RTL2838UHIDIR' or similar).
  3. Driver dropdown (right side): WinUSB.
  4. Click 'Install Driver' (or 'Replace Driver').
  5. Wait ~30s. Close Zadig.
  6. Unplug + replug the dongle, then verify with:  rf-agent doctor

NOTE: this replaces the Realtek TV-tuner driver. To use the dongle as
a TV tuner again, re-install the Realtek driver from Device Manager.
For SDR use, WinUSB is the right driver.
====================================================================
"""
    )
    print(f"[zadig] launching {zadig_path} ...")
    # os.startfile only exists on Windows; we already exited above on non-win32.
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        print(
            "Error: os.startfile unavailable; launch Zadig manually.", file=sys.stderr
        )
        return 1
    try:
        startfile(str(zadig_path))
    except OSError as e:
        print(f"Error launching Zadig: {e}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def add_setup_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    p = sub.add_parser(
        "setup",
        help="Install OS-level RTL-SDR prerequisites (udev rules, libusb, Zadig)",
        description=(
            "Run the platform-specific setup that `pip install` cannot do "
            "(udev rules, package-manager libusb install, driver replacement). "
            "Auto-detects the platform if PLATFORM is omitted."
        ),
    )
    p.add_argument(
        "platform",
        nargs="?",
        choices=["linux", "macos", "windows", "wsl-attach"],
        default=None,
        help=(
            "Force a specific handler. 'wsl-attach' (WSL only) re-attaches "
            "the RTL-SDR dongle via usbipd-win. Default: auto-detect."
        ),
    )


def _detect() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    return "linux"


def run(args: argparse.Namespace) -> None:
    target = args.platform or _detect()
    print(f"rf-agent setup ({target})")
    print()
    if target == "linux":
        rc = setup_linux()
    elif target == "macos":
        rc = setup_macos()
    elif target == "windows":
        rc = setup_windows()
    elif target == "wsl-attach":
        rc = setup_wsl_attach()
    else:
        print(f"Unsupported platform: {target}", file=sys.stderr)
        rc = 1
    raise SystemExit(rc)


# ---------------------------------------------------------------------------
# WSL: attach the RTL-SDR via usbipd-win (Windows-side binary, invoked from WSL)
# ---------------------------------------------------------------------------


_USBIPD_PATH = "/mnt/c/Program Files/usbipd-win/usbipd.exe"
_KNOWN_RTLSDR_IDS = ("0bda:2838", "0bda:2832", "0bda:2831")


def setup_wsl_attach(wsl_distro: str | None = None) -> int:
    """Find the RTL-SDR via usbipd-win and attach it to the current WSL distro."""
    if not Path(_USBIPD_PATH).is_file():
        print(
            f"Error: usbipd-win not found at {_USBIPD_PATH}.\n"
            "Install from https://github.com/dorssel/usbipd-win/releases",
            file=sys.stderr,
        )
        return 1
    distro = wsl_distro or os.environ.get("WSL_DISTRO_NAME") or "Ubuntu"

    try:
        listing = subprocess.run(
            [_USBIPD_PATH, "list"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.replace("\r", "")
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"Error: `usbipd list` failed: {e}", file=sys.stderr)
        return 1

    busid: str | None = None
    matched: str | None = None
    for vid_pid in _KNOWN_RTLSDR_IDS:
        for line in listing.splitlines():
            if vid_pid.lower() in line.lower():
                busid = line.split()[0]
                matched = vid_pid
                break
        if busid:
            break

    if not busid:
        print(
            f"RTL-SDR not found. Tried: {', '.join(_KNOWN_RTLSDR_IDS)}\n",
            file=sys.stderr,
        )
        print(listing, file=sys.stderr)
        return 1

    print(f"Found {matched} at busid {busid} -> attaching to WSL distro '{distro}'")
    try:
        attach = subprocess.run(
            [_USBIPD_PATH, "attach", "--wsl", distro, "--busid", busid],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        print(f"Error launching usbipd: {e}", file=sys.stderr)
        return 1
    out = (attach.stdout + attach.stderr).replace("\r", "")
    print(out)
    if "not shared" in out.lower():
        print(
            "Device isn't shared yet. From an ELEVATED Windows PowerShell, run:\n"
            f"  usbipd bind --busid {busid}",
            file=sys.stderr,
        )
        return 1
    if attach.returncode != 0:
        return attach.returncode
    return 0
