"""Unit tests for `rf-agent doctor`.

Each check is exercised in isolation by mocking the surface it depends on
(ctypes.util.find_library, subprocess, the rtlsdr import, file system). The
goal is to assert that each scenario produces the right Status and a remedy
the user can act on.
"""

from __future__ import annotations

import sys
from collections import namedtuple
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

from agent.doctor import (
    Check,
    Report,
    Status,
    check_brew_libusb,
    check_device_enumeration,
    check_libusb,
    check_plugdev,
    check_python,
    check_udev_rules,
    detect_platform,
    format_report,
    run_diagnostics,
)

_PyVer = namedtuple("_PyVer", ["major", "minor", "micro"])


# ---------------------------------------------------------------------------
# Report aggregation
# ---------------------------------------------------------------------------


def test_report_worst_status_is_highest_severity() -> None:
    rep = Report(platform="linux")
    rep.add(Check("a", Status.OK, ""))
    rep.add(Check("b", Status.WARN, ""))
    rep.add(Check("c", Status.INFO, ""))
    assert rep.worst is Status.WARN

    rep.add(Check("d", Status.FAIL, "", remedy="fix it"))
    assert rep.worst is Status.FAIL


def test_report_worst_is_ok_when_empty() -> None:
    assert Report(platform="linux").worst is Status.OK


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def test_detect_platform_macos() -> None:
    with patch("agent.doctor.sys.platform", "darwin"):
        assert detect_platform() == "macos"


def test_detect_platform_windows() -> None:
    with patch("agent.doctor.sys.platform", "win32"):
        assert detect_platform() == "windows"


def test_detect_platform_linux_native(tmp_path: Path) -> None:
    fake_release = tmp_path / "osrelease"
    fake_release.write_text("6.6.0-generic\n")
    with (
        patch("agent.doctor.sys.platform", "linux"),
        patch("agent.doctor.Path") as MockPath,
    ):
        MockPath.return_value = fake_release
        assert detect_platform() == "linux"


def test_detect_platform_wsl(tmp_path: Path) -> None:
    fake_release = tmp_path / "osrelease"
    fake_release.write_text("6.6.87-microsoft-standard-WSL2\n")
    with (
        patch("agent.doctor.sys.platform", "linux"),
        patch("agent.doctor.Path") as MockPath,
    ):
        MockPath.return_value = fake_release
        assert detect_platform() == "wsl"


# ---------------------------------------------------------------------------
# Python check
# ---------------------------------------------------------------------------


def test_check_python_ok_for_310() -> None:
    with patch("agent.doctor.sys.version_info", _PyVer(3, 10, 0)):
        c = check_python()
    assert c.status is Status.OK
    assert c.remedy is None


def test_check_python_fail_for_old() -> None:
    with patch("agent.doctor.sys.version_info", _PyVer(3, 8, 0)):
        c = check_python()
    assert c.status is Status.FAIL
    assert c.remedy is not None


# ---------------------------------------------------------------------------
# libusb check
# ---------------------------------------------------------------------------


def test_check_libusb_missing_linux_suggests_apt() -> None:
    with patch("agent.doctor.ctypes.util.find_library", return_value=None):
        c = check_libusb("linux")
    assert c.status is Status.FAIL
    assert c.remedy is not None
    assert "libusb" in c.remedy.lower()


def test_check_libusb_missing_macos_suggests_brew() -> None:
    with patch("agent.doctor.ctypes.util.find_library", return_value=None):
        c = check_libusb("macos")
    assert c.status is Status.FAIL
    assert c.remedy is not None
    assert "brew" in c.remedy.lower()


def test_check_libusb_windows_no_path_is_info_not_warn() -> None:
    """On Windows, libusb-1.0.dll is bundled inside pyrtlsdrlib's package
    directory rather than on system PATH. Not finding it via find_library
    is the normal case and shouldn't generate a WARN with a misleading
    'run Zadig' remedy — Zadig is for the WinUSB *driver*, not libusb."""
    with patch("agent.doctor.ctypes.util.find_library", return_value=None):
        c = check_libusb("windows")
    assert c.status is Status.INFO
    assert c.remedy is None
    assert "bundled" in c.detail.lower()


def test_check_libusb_found_and_loadable() -> None:
    with (
        patch("agent.doctor.ctypes.util.find_library", return_value="libusb-1.0.so.0"),
        patch("agent.doctor.ctypes.CDLL") as MockCDLL,
    ):
        MockCDLL.return_value = object()
        c = check_libusb("linux")
    assert c.status is Status.OK
    assert "libusb-1.0.so.0" in c.detail


def test_check_libusb_found_but_load_fails() -> None:
    with (
        patch("agent.doctor.ctypes.util.find_library", return_value="libusb-1.0.so.0"),
        patch("agent.doctor.ctypes.CDLL", side_effect=OSError("boom")),
    ):
        c = check_libusb("linux")
    assert c.status is Status.FAIL


# ---------------------------------------------------------------------------
# Device enumeration check
# ---------------------------------------------------------------------------


def _install_fake_rtlsdr(device_count: int) -> dict[str, ModuleType]:
    """Build minimal `rtlsdr` + `rtlsdr.librtlsdr` modules and return them as
    a sys.modules-compatible dict for patching."""
    pkg = ModuleType("rtlsdr")
    pkg.__path__ = []  # type: ignore[attr-defined]
    lib = ModuleType("rtlsdr.librtlsdr")
    lib.rtlsdr_get_device_count = staticmethod(lambda: device_count)  # type: ignore[attr-defined]
    pkg.librtlsdr = lib  # type: ignore[attr-defined]
    return {"rtlsdr": pkg, "rtlsdr.librtlsdr": lib}


def test_check_device_enumeration_zero_suggests_attach() -> None:
    with patch.dict(sys.modules, _install_fake_rtlsdr(0)):
        c = check_device_enumeration()
    assert c.status is Status.WARN
    assert c.remedy is not None
    assert "wsl-attach" in c.remedy or "Plug" in c.remedy


def test_check_device_enumeration_one_device_ok() -> None:
    with patch.dict(sys.modules, _install_fake_rtlsdr(1)):
        c = check_device_enumeration()
    assert c.status is Status.OK
    assert "1 device" in c.detail


# ---------------------------------------------------------------------------
# udev rules
# ---------------------------------------------------------------------------


def test_check_udev_rules_missing(tmp_path: Path) -> None:
    with patch("agent.doctor.Path") as MockPath:
        MockPath.return_value = tmp_path  # empty dir
        c = check_udev_rules()
    assert c.status is Status.WARN
    assert c.remedy is not None
    assert "rf-agent setup linux" in c.remedy


def test_check_udev_rules_present(tmp_path: Path) -> None:
    (tmp_path / "20-rtl-sdr.rules").write_text("")
    with patch("agent.doctor.Path") as MockPath:
        MockPath.return_value = tmp_path
        c = check_udev_rules()
    assert c.status is Status.OK


# ---------------------------------------------------------------------------
# plugdev membership
# ---------------------------------------------------------------------------


_PLUGDEV_GID = 46  # arbitrary; what matters is consistency within each test


def _fake_grp(members: list[str]) -> ModuleType:
    """Build a stand-in `grp` module that returns a struct-like group entry."""

    class _Group:
        gr_gid = _PLUGDEV_GID
        gr_mem = members

    mod = ModuleType("grp")
    mod.getgrnam = lambda _name: _Group  # type: ignore[attr-defined]
    return mod


def _fake_pwd(primary_gid: int) -> ModuleType:
    """Build a stand-in `pwd` module that reports the given primary gid."""

    class _Pwd:
        pw_gid = primary_gid

    mod = ModuleType("pwd")
    mod.getpwnam = lambda _name: _Pwd  # type: ignore[attr-defined]
    return mod


def test_check_plugdev_supplementary_member_is_ok() -> None:
    """Case 3: user listed in gr_mem (typical post-usermod, post-relogin)."""
    with (
        patch("agent.doctor.os.environ", {"USER": "alice"}),
        patch("agent.doctor.os.getgroups", return_value=[]),
        patch.dict(
            sys.modules,
            {"grp": _fake_grp(["alice", "bob"]), "pwd": _fake_pwd(primary_gid=1000)},
        ),
    ):
        c = check_plugdev()
    assert c.status is Status.OK
    assert "alice" in c.detail


def test_check_plugdev_primary_group_is_ok() -> None:
    """Case 2: plugdev is the user's primary group, even if not in gr_mem."""
    with (
        patch("agent.doctor.os.environ", {"USER": "alice"}),
        patch("agent.doctor.os.getgroups", return_value=[]),
        patch.dict(
            sys.modules,
            {"grp": _fake_grp([]), "pwd": _fake_pwd(primary_gid=_PLUGDEV_GID)},
        ),
    ):
        c = check_plugdev()
    assert c.status is Status.OK


def test_check_plugdev_in_effective_groups_is_ok() -> None:
    """Case 1: plugdev gid is in the running process's getgroups()."""
    with (
        patch("agent.doctor.os.environ", {"USER": "alice"}),
        patch("agent.doctor.os.getgroups", return_value=[_PLUGDEV_GID]),
        patch.dict(
            sys.modules,
            {"grp": _fake_grp([]), "pwd": _fake_pwd(primary_gid=1000)},
        ),
    ):
        c = check_plugdev()
    assert c.status is Status.OK


def test_check_plugdev_user_not_member_suggests_usermod() -> None:
    with (
        patch("agent.doctor.os.environ", {"USER": "alice"}),
        patch("agent.doctor.os.getgroups", return_value=[]),
        patch.dict(
            sys.modules,
            {
                "grp": _fake_grp(["someone-else"]),
                "pwd": _fake_pwd(primary_gid=1000),
            },
        ),
    ):
        c = check_plugdev()
    assert c.status is Status.WARN
    assert c.remedy is not None
    assert "alice" in c.remedy


def test_check_plugdev_group_missing_returns_info() -> None:
    def raise_keyerror(_: str) -> None:
        raise KeyError("plugdev")

    fake_grp = ModuleType("grp")
    fake_grp.getgrnam = raise_keyerror  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"grp": fake_grp}):
        c = check_plugdev()
    assert c.status is Status.INFO


def test_check_plugdev_sudo_user_overrides_user() -> None:
    """Under sudo, USER='root' but we want to check the original invoker."""
    with (
        patch(
            "agent.doctor.os.environ",
            {"USER": "root", "SUDO_USER": "alice"},
        ),
        patch("agent.doctor.os.getgroups", return_value=[]),
        patch.dict(
            sys.modules,
            {"grp": _fake_grp(["alice"]), "pwd": _fake_pwd(primary_gid=1000)},
        ),
    ):
        c = check_plugdev()
    assert c.status is Status.OK
    assert "alice" in c.detail


# ---------------------------------------------------------------------------
# brew libusb (macOS)
# ---------------------------------------------------------------------------


def test_check_brew_libusb_no_brew() -> None:
    with patch("agent.doctor.shutil.which", return_value=None):
        c = check_brew_libusb()
    assert c.status is Status.WARN
    assert c.remedy is not None
    assert "brew" in c.remedy.lower()


def test_check_brew_libusb_installed() -> None:
    fake_proc = type("P", (), {"returncode": 0})()
    with (
        patch("agent.doctor.shutil.which", return_value="/usr/local/bin/brew"),
        patch("agent.doctor.subprocess.run", return_value=fake_proc),
    ):
        c = check_brew_libusb()
    assert c.status is Status.OK


def test_check_brew_libusb_missing_suggests_install() -> None:
    fake_proc = type("P", (), {"returncode": 1})()
    with (
        patch("agent.doctor.shutil.which", return_value="/usr/local/bin/brew"),
        patch("agent.doctor.subprocess.run", return_value=fake_proc),
    ):
        c = check_brew_libusb()
    assert c.status is Status.WARN
    assert c.remedy is not None
    assert "brew install" in c.remedy


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def test_run_diagnostics_no_device_skips_hardware_probes() -> None:
    """--no-device must skip device enumeration so CI passes without hardware."""
    rep = run_diagnostics(no_device=True)
    names = {c.name for c in rep.checks}
    assert "device enumeration" not in names
    assert "device open" not in names


def test_format_report_includes_status_markers_and_remedies() -> None:
    rep = Report(platform="linux")
    rep.add(Check("Python >= 3.10", Status.OK, "3.12.0"))
    rep.add(
        Check(
            "libusb-1.0", Status.FAIL, "missing", remedy="sudo apt install libusb-1.0-0"
        )
    )
    out = format_report(rep)
    assert "OK" in out
    assert "FAIL" in out
    assert "Next steps:" in out
    assert "libusb-1.0" in out
    assert "sudo apt install libusb-1.0-0" in out
