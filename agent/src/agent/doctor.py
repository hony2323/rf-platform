"""rf-agent doctor — diagnose RTL-SDR setup and print platform-specific remedies.

Runs read-only checks (libusb, pyrtlsdr import, USB permissions, device
enumeration) and emits a status table plus a 'Next steps' block with
copy-pasteable commands tailored to the detected platform (linux, wsl,
windows, macos).

The checks are pure functions that return :class:`Check` records, so they can
be unit-tested without hardware.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import importlib.metadata
import os
import platform as _platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(Enum):
    OK = "OK"
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"


_STATUS_ORDER = {Status.OK: 0, Status.INFO: 1, Status.WARN: 2, Status.FAIL: 3}
_STATUS_MARKER = {
    Status.OK: "OK  ",
    Status.INFO: "INFO",
    Status.WARN: "WARN",
    Status.FAIL: "FAIL",
}


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    remedy: str | None = None


@dataclass
class Report:
    platform: str
    checks: list[Check] = field(default_factory=list)

    def add(self, c: Check) -> None:
        self.checks.append(c)

    @property
    def worst(self) -> Status:
        if not self.checks:
            return Status.OK
        return max((c.status for c in self.checks), key=lambda s: _STATUS_ORDER[s])


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def detect_platform() -> str:
    """Return one of: 'linux', 'wsl', 'windows', 'macos', 'unknown'."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        try:
            release = Path("/proc/sys/kernel/osrelease").read_text().lower()
            if "microsoft" in release:
                return "wsl"
        except OSError:
            pass
        return "linux"
    return "unknown"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_python() -> Check:
    py = sys.version_info
    ok = py >= (3, 10)
    return Check(
        name="Python >= 3.10",
        status=Status.OK if ok else Status.FAIL,
        detail=f"{py.major}.{py.minor}.{py.micro}",
        remedy=None if ok else "Upgrade Python to 3.10 or newer.",
    )


def check_libusb(plat: str) -> Check:
    soname = ctypes.util.find_library("usb-1.0")
    if soname is None:
        if plat in ("linux", "wsl"):
            return Check(
                "libusb-1.0",
                Status.FAIL,
                "shared library not found",
                remedy="sudo apt install libusb-1.0-0   # or: rf-agent setup linux",
            )
        if plat == "macos":
            return Check(
                "libusb-1.0",
                Status.FAIL,
                "dylib not found",
                remedy="brew install libusb   # or: rf-agent setup macos",
            )
        if plat == "windows":
            return Check(
                "libusb-1.0",
                Status.WARN,
                "DLL not on PATH (often bundled inside librtlsdr.dll)",
                remedy="Run Zadig to install the WinUSB driver: rf-agent setup windows",
            )
        return Check("libusb-1.0", Status.WARN, "find_library returned None")
    try:
        ctypes.CDLL(soname)
        return Check("libusb-1.0", Status.OK, soname)
    except OSError as e:
        return Check("libusb-1.0", Status.FAIL, f"load failed: {e}")


def check_pyrtlsdr_import() -> Check:
    """Import rtlsdr and report which librtlsdr backend got loaded."""
    # Install symbol stubs as defence-in-depth before the import, in case the
    # user is on a system librtlsdr that lacks the optional symbols.
    try:
        from agent.source.rtl_sdr_source import _install_missing_symbol_stubs

        _install_missing_symbol_stubs()
    except Exception:
        pass

    try:
        import rtlsdr  # noqa: F401
    except ImportError as e:
        return Check(
            "pyrtlsdr import",
            Status.FAIL,
            str(e),
            remedy="pip install 'rf-agent[sdr]'",
        )
    except AttributeError as e:
        # Raised when librtlsdr is loaded but missing symbols (and our stub
        # patch somehow didn't run first).
        return Check(
            "pyrtlsdr import",
            Status.FAIL,
            f"librtlsdr missing symbols: {e}",
            remedy="pip install pyrtlsdrlib   # bundled binary has full symbol set",
        )

    try:
        import pyrtlsdrlib  # type: ignore[import-not-found]  # noqa: F401

        backing = "pyrtlsdrlib (bundled)"
    except ImportError:
        backing = "system librtlsdr"
    return Check("pyrtlsdr import", Status.OK, backing)


def check_device_enumeration() -> Check:
    try:
        # rtlsdr_get_device_count is bound as a module attribute on
        # rtlsdr.librtlsdr (via getattr, not direct `from … import`).
        import rtlsdr.librtlsdr as _lib  # type: ignore[import-not-found]

        get_count = getattr(_lib, "rtlsdr_get_device_count", None)
        if get_count is None:
            return Check(
                "device enumeration",
                Status.FAIL,
                "rtlsdr_get_device_count not found in rtlsdr.librtlsdr",
            )
        count = int(get_count())
    except ImportError as e:
        return Check("device enumeration", Status.FAIL, str(e))
    except Exception as e:  # pragma: no cover — driver issues vary
        return Check("device enumeration", Status.FAIL, str(e))

    if count == 0:
        return Check(
            "device enumeration",
            Status.WARN,
            "0 device(s) found",
            remedy=(
                "Plug in an RTL-SDR dongle. On WSL, attach it with: "
                "rf-agent setup wsl-attach"
            ),
        )
    return Check("device enumeration", Status.OK, f"{count} device(s) found")


def check_device_open() -> Check:
    """Briefly open device index 0; translate LIBUSB errors into remedies."""
    try:
        from rtlsdr import RtlSdr  # type: ignore[import-not-found]
    except ImportError as e:
        return Check("device open", Status.FAIL, str(e))

    try:
        sdr = RtlSdr(0)
        try:
            sdr.close()
        except Exception:
            pass
        return Check("device open", Status.OK, "opened device 0")
    except Exception as e:
        msg = str(e)
        if "LIBUSB_ERROR_ACCESS" in msg or "Permission denied" in msg:
            return Check(
                "device open",
                Status.FAIL,
                msg,
                remedy=(
                    "Install udev rules so non-root users can access the dongle:\n"
                    "rf-agent setup linux"
                ),
            )
        if "LIBUSB_ERROR_BUSY" in msg or "Resource busy" in msg:
            return Check(
                "device open",
                Status.FAIL,
                msg,
                remedy=(
                    "Another process holds the dongle. Try:\n"
                    "sudo rmmod dvb_usb_rtl28xxu rtl2832 rtl2830 2>/dev/null\n"
                    "pkill -f gqrx; pkill -f rtl_tcp\n"
                    "...or unplug/replug, then re-run."
                ),
            )
        if "LIBUSB_ERROR_NOT_SUPPORTED" in msg or "LIBUSB_ERROR_NOT_FOUND" in msg:
            return Check(
                "device open",
                Status.FAIL,
                msg,
                remedy=(
                    "Windows: replace the Realtek driver with WinUSB via Zadig:\n"
                    "rf-agent setup windows"
                ),
            )
        return Check("device open", Status.FAIL, msg)


def check_udev_rules() -> Check:
    rules_dir = Path("/etc/udev/rules.d")
    try:
        rules = sorted(
            list(rules_dir.glob("*rtl*")) + list(rules_dir.glob("*rtl-sdr*"))
        )
    except OSError:
        rules = []
    if not rules:
        return Check(
            "udev rules",
            Status.WARN,
            f"no *rtl* rules in {rules_dir}",
            remedy=(
                "rf-agent setup linux   # installs /etc/udev/rules.d/20-rtl-sdr.rules"
            ),
        )
    return Check("udev rules", Status.OK, str(rules[0]))


def check_plugdev() -> Check:
    try:
        import grp

        members = grp.getgrnam("plugdev").gr_mem
    except (KeyError, ImportError):
        return Check(
            "plugdev group", Status.INFO, "group does not exist on this system"
        )
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if user and user in members:
        return Check("plugdev group", Status.OK, f"user '{user}' is a member")
    return Check(
        "plugdev group",
        Status.WARN,
        f"user '{user}' is not in plugdev",
        remedy=(
            f"sudo usermod -aG plugdev {user}   # then logout/login "
            "(or: rf-agent setup linux)"
        ),
    )


def check_brew_libusb() -> Check:
    brew = shutil.which("brew")
    if brew is None:
        return Check(
            "brew libusb",
            Status.WARN,
            "Homebrew not found",
            remedy=(
                "Install Homebrew from https://brew.sh then run: rf-agent setup macos"
            ),
        )
    try:
        out = subprocess.run(
            [brew, "list", "libusb"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0:
            return Check("brew libusb", Status.OK, "installed")
    except (OSError, subprocess.SubprocessError):
        pass
    return Check(
        "brew libusb",
        Status.WARN,
        "not installed",
        remedy="brew install libusb   # or: rf-agent setup macos",
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_diagnostics(no_device: bool = False) -> Report:
    """Run every applicable check and return a Report."""
    plat = detect_platform()
    rep = Report(platform=plat)
    rep.add(check_python())
    rep.add(check_libusb(plat))
    rep.add(check_pyrtlsdr_import())

    if plat in ("linux", "wsl"):
        rep.add(check_udev_rules())
        rep.add(check_plugdev())
    if plat == "macos":
        rep.add(check_brew_libusb())

    if not no_device:
        # Only attempt USB device interaction if the prerequisites are sane.
        prereq_failed = any(
            c.status == Status.FAIL and c.name in ("pyrtlsdr import", "libusb-1.0")
            for c in rep.checks
        )
        if not prereq_failed:
            enum = check_device_enumeration()
            rep.add(enum)
            if enum.status == Status.OK:
                rep.add(check_device_open())
    return rep


def format_report(rep: Report) -> str:
    try:
        version = importlib.metadata.version("rf-agent")
    except importlib.metadata.PackageNotFoundError:
        version = "?"

    lines: list[str] = []
    lines.append(f"rf-agent doctor v{version}")
    lines.append(f"Platform: {rep.platform}  ({_platform.platform()})")
    lines.append("")

    name_w = max((len(c.name) for c in rep.checks), default=0)
    for c in rep.checks:
        lines.append(
            f"  [{_STATUS_MARKER[c.status]}]  {c.name.ljust(name_w)}  {c.detail}"
        )
    lines.append("")

    remedies = [c for c in rep.checks if c.remedy]
    if remedies:
        lines.append("Next steps:")
        for i, c in enumerate(remedies, 1):
            indented = "\n        ".join(c.remedy.splitlines())  # type: ignore[union-attr]
            lines.append(f"  {i}. {c.name}:")
            lines.append(f"        {indented}")
    elif rep.worst == Status.OK:
        lines.append("Everything looks good. Try:")
        lines.append("  rf-agent connect --source rtl-sdr --freq 100000000")
    return "\n".join(lines)


def add_doctor_subparser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "doctor",
        help="Diagnose RTL-SDR setup and print platform-specific fix instructions",
        description=(
            "Check whether the RTL-SDR dependency chain is healthy and, "
            "if not, print the exact commands needed to fix it."
        ),
    )
    p.add_argument(
        "--no-device",
        action="store_true",
        help=(
            "Skip USB device enumeration / open checks. "
            "Useful in CI environments without hardware."
        ),
    )


def run(args: argparse.Namespace) -> None:
    rep = run_diagnostics(no_device=getattr(args, "no_device", False))
    print(format_report(rep))
    if rep.worst == Status.FAIL:
        raise SystemExit(1)
