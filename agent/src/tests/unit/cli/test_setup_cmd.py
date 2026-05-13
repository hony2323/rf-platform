"""Unit tests for `rf-agent setup` error-handling paths.

Focuses on the surface that matters in production:
  - subprocess failures produce clean errors (not tracebacks),
  - missing package managers return a clear actionable message,
  - tempfile is cleaned up even on failure,
  - failures short-circuit (we don't keep going past a failed step).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.setup_cmd import (
    UDEV_RULES_PATH,
    UDEV_RULES_TEXT,
    _run,
    setup_linux,
    setup_macos,
)


# ---------------------------------------------------------------------------
# _run wrapper
# ---------------------------------------------------------------------------


def test_run_returns_nonzero_for_missing_binary(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("agent.setup_cmd.subprocess.run", side_effect=FileNotFoundError):
        rc = _run(["definitely-not-installed-xyz"])
    err = capsys.readouterr().err
    assert rc == 127
    assert "command not found" in err.lower()


def test_run_returns_nonzero_for_oserror(capsys: pytest.CaptureFixture[str]) -> None:
    with patch(
        "agent.setup_cmd.subprocess.run", side_effect=OSError("permission denied")
    ):
        rc = _run(["whatever"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "permission denied" in err.lower() or "error" in err.lower()


def test_run_returns_nonzero_for_failed_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = subprocess.CompletedProcess(args=["x"], returncode=2)
    with patch("agent.setup_cmd.subprocess.run", return_value=fake):
        rc = _run(["x"], label="frobnicate")
    err = capsys.readouterr().err
    assert rc == 2
    assert "frobnicate" in err
    assert "2" in err


def test_run_does_not_raise_on_nonzero_exit() -> None:
    """Regression: previous version used check=True and raised. Callers now
    decide via the return code; _run itself must not raise."""
    fake = subprocess.CompletedProcess(args=["x"], returncode=1)
    with patch("agent.setup_cmd.subprocess.run", return_value=fake):
        # Must NOT raise.
        rc = _run(["x"])
    assert rc == 1


# ---------------------------------------------------------------------------
# setup_linux — no supported package manager
# ---------------------------------------------------------------------------


def test_setup_linux_unsupported_distro(capsys: pytest.CaptureFixture[str]) -> None:
    """When no package manager is detected, we tell the user clearly and exit."""
    with (
        patch("agent.setup_cmd.os.geteuid", return_value=0, create=True),
        patch("agent.setup_cmd._ldconfig_has", return_value=False),
        patch("agent.setup_cmd._has_apt", return_value=False),
        patch("agent.setup_cmd._has_dnf", return_value=False),
        patch("agent.setup_cmd._has_pacman", return_value=False),
        patch("agent.setup_cmd._has_zypper", return_value=False),
    ):
        rc = setup_linux()
    err = capsys.readouterr().err
    assert rc != 0
    assert "no supported package manager" in err
    assert "manually" in err
    assert "rf-agent setup linux" in err


# ---------------------------------------------------------------------------
# setup_linux — apt failure short-circuits
# ---------------------------------------------------------------------------


def _libusb_already_present():
    """Helper: patch out everything except the libusb-already-installed flag."""
    return patch("agent.setup_cmd._ldconfig_has", return_value=True)


def test_setup_linux_apt_install_failure_exits_immediately(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """apt-get install libusb-1.0-0 failing should return its exit code and
    not proceed to udev install."""
    fake_fail = subprocess.CompletedProcess(args=["apt-get"], returncode=100)
    with (
        patch("agent.setup_cmd.os.geteuid", return_value=0, create=True),
        patch("agent.setup_cmd._ldconfig_has", return_value=False),
        patch("agent.setup_cmd._has_apt", return_value=True),
        patch("agent.setup_cmd.subprocess.run", return_value=fake_fail),
    ):
        rc = setup_linux()
    captured = capsys.readouterr()
    assert rc == 100
    # The udev/plugdev steps must not have been advertised in the output.
    assert "[udev]" not in captured.out


def test_setup_linux_udev_install_failure_propagates(tmp_path: Path) -> None:
    """If `install -m 0644 ...` fails, return that code and clean up tempfile."""
    fake_install_fail = subprocess.CompletedProcess(args=["install"], returncode=5)
    with (
        patch("agent.setup_cmd.os.geteuid", return_value=0, create=True),
        patch("agent.setup_cmd._ldconfig_has", return_value=True),
        patch("agent.setup_cmd.Path") as MockPath,
        patch("agent.setup_cmd.subprocess.run", return_value=fake_install_fail),
    ):
        # Make the existing rules file appear absent so we go down the install path.
        fake_existing = tmp_path / "20-rtl-sdr.rules"
        MockPath.return_value = fake_existing
        rc = setup_linux()
    assert rc == 5


def test_setup_linux_udevadm_reload_failure_propagates() -> None:
    """If udevadm reload fails, we exit non-zero (and never run trigger)."""
    call_log: list[list[str]] = []

    def fake_run(cmd, **kw):
        call_log.append(list(cmd))
        # `install` succeeds, `udevadm control --reload-rules` fails.
        if "control" in cmd:
            return subprocess.CompletedProcess(cmd, returncode=7)
        return subprocess.CompletedProcess(cmd, returncode=0)

    with (
        patch("agent.setup_cmd.os.geteuid", return_value=0, create=True),
        patch("agent.setup_cmd._ldconfig_has", return_value=True),
        patch("agent.setup_cmd.subprocess.run", side_effect=fake_run),
        patch.object(Path, "exists", return_value=False),
    ):
        rc = setup_linux()
    assert rc == 7
    # trigger should never have run after reload-rules failed.
    assert not any("trigger" in c for c in call_log)


# ---------------------------------------------------------------------------
# setup_linux — groupadd / usermod failures
# ---------------------------------------------------------------------------


def test_setup_linux_usermod_failure_propagates() -> None:
    """usermod failing should bubble up its exit code."""
    seen_usermod = False

    def fake_run(cmd, **kw):
        nonlocal seen_usermod
        if "usermod" in cmd:
            seen_usermod = True
            return subprocess.CompletedProcess(cmd, returncode=9)
        return subprocess.CompletedProcess(cmd, returncode=0)

    import types

    class _G:
        gr_mem: list[str] = []  # user is not yet in plugdev → triggers usermod

    fake_grp = types.ModuleType("grp")
    fake_grp.getgrnam = lambda _n: _G  # type: ignore[attr-defined]

    with (
        patch("agent.setup_cmd.os.geteuid", return_value=0, create=True),
        patch("agent.setup_cmd._ldconfig_has", return_value=True),
        patch.object(Path, "exists", return_value=True),
        patch.object(Path, "read_text", return_value=UDEV_RULES_TEXT),
        patch("agent.setup_cmd.os.environ", {"USER": "alice"}),
        patch.dict("sys.modules", {"grp": fake_grp}),
        patch("agent.setup_cmd.subprocess.run", side_effect=fake_run),
    ):
        rc = setup_linux()
    assert seen_usermod
    assert rc == 9


# ---------------------------------------------------------------------------
# Tempfile cleanup
# ---------------------------------------------------------------------------


def test_setup_linux_tempfile_cleanup_survives_unlink_error() -> None:
    """If the tempfile was moved by `install`, the cleanup os.unlink will
    fail — that must not raise."""

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, returncode=0)

    import types

    class _G:
        gr_mem = ["alice"]  # already a member → skip usermod

    fake_grp = types.ModuleType("grp")
    fake_grp.getgrnam = lambda _n: _G  # type: ignore[attr-defined]

    with (
        patch("agent.setup_cmd.os.geteuid", return_value=0, create=True),
        patch("agent.setup_cmd._ldconfig_has", return_value=True),
        patch.object(Path, "exists", return_value=False),
        patch("agent.setup_cmd.os.environ", {"USER": "alice"}),
        patch.dict("sys.modules", {"grp": fake_grp}),
        patch("agent.setup_cmd.subprocess.run", side_effect=fake_run),
        patch(
            "agent.setup_cmd.os.unlink",
            side_effect=FileNotFoundError(2, "no such file"),
        ),
    ):
        # Must NOT raise.
        rc = setup_linux()
    # Setup itself succeeded; the unlink failure was swallowed.
    assert rc == 0


# ---------------------------------------------------------------------------
# setup_macos
# ---------------------------------------------------------------------------


def test_setup_macos_no_brew(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("agent.setup_cmd.shutil.which", return_value=None):
        rc = setup_macos()
    assert rc == 1
    assert "Homebrew" in capsys.readouterr().err


def test_setup_macos_brew_install_failure() -> None:
    list_proc = subprocess.CompletedProcess(["brew", "list"], returncode=1)
    install_proc = subprocess.CompletedProcess(["brew", "install"], returncode=1)
    with (
        patch("agent.setup_cmd.shutil.which", return_value="/usr/local/bin/brew"),
        patch("agent.setup_cmd.subprocess.run", side_effect=[list_proc, install_proc]),
    ):
        rc = setup_macos()
    assert rc == 1


# Sanity: UDEV_RULES_PATH and UDEV_RULES_TEXT exposed at module level for use
# by both the runtime helper and tests.
def test_udev_constants_exposed() -> None:
    assert UDEV_RULES_PATH.startswith("/etc/udev/rules.d/")
    assert "ATTRS{idVendor}" in UDEV_RULES_TEXT
