"""T5.1: per-OS shell selection (D6, D17). Linux and macOS use bash; Windows pwsh."""

from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import sys
import time

import pytest

from rtfmup.execute import shells
from rtfmup.execute.runner import run_step
from rtfmup.execute.shells import CommandError, build_command, find_bash
from rtfmup.plan.model import Step

UNIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def shell_step(text: str) -> Step:
    return Step(id="s", kind="setup", title="t", shell_text=text)


def test_argv_step_is_resolved_and_not_shelled():
    step = Step(id="s", kind="setup", title="t", argv=[sys.executable, "-V"])
    assert build_command(step)[1:] == ["-V"]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_needs_shell_runs_through_bash_dash_c(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(shutil, "which", lambda name, path=None: f"/x/{name}")
    monkeypatch.setattr(os, "access", lambda p, m: False)  # no /bin/bash here
    assert build_command(shell_step("a | b")) == ["/x/bash", "-c", "a | b"]


def test_macos_uses_the_system_bash_not_one_from_homebrew(monkeypatch):
    """/bin/bash is 3.2 on every Mac; a Homebrew bash 5 first on PATH must not win."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os, "access", lambda p, m: p == "/bin/bash")
    monkeypatch.setattr(
        shutil, "which", lambda name, path=None: "/opt/homebrew/bin/bash"
    )
    assert build_command(shell_step("true"))[0] == shells.MACOS_BASH == "/bin/bash"


def test_linux_never_pins_bin_bash(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name, path=None: "/usr/local/bin/bash")
    assert find_bash() == "/usr/local/bin/bash"


def test_missing_bash_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None)
    with pytest.raises(CommandError, match="bash is required"):
        build_command(shell_step("true"))


@UNIX
def test_a_needs_shell_step_really_runs_under_bash(tmp_path):
    """Pipes, `&&` and env assignment: what argv cannot do. Stays bash-3.2 clean."""
    step = shell_step("X=41 && echo $((X + 1)) | tr 4 5 && echo done >&2")
    out, err = io.StringIO(), io.StringIO()
    result = run_step(step, tmp_path, out=out, err=err)
    assert result.ok, err.getvalue()
    assert out.getvalue().split() == ["52"]
    assert "done" in err.getvalue()


@UNIX
def test_needs_shell_exit_code_propagates(tmp_path):
    result = run_step(
        shell_step("exit 9"), tmp_path, out=io.StringIO(), err=io.StringIO()
    )
    assert result.status == "failed" and result.exit_code == 9


@pytest.mark.skipif(
    sys.platform != "win32" or not shutil.which("pwsh"), reason="needs pwsh on Windows"
)
def test_a_needs_shell_step_runs_under_pwsh_on_windows(tmp_path):
    out = io.StringIO()
    result = run_step(
        shell_step("Write-Output (1 + 41)"), tmp_path, out=out, err=io.StringIO()
    )
    assert result.ok and out.getvalue().split() == ["42"]


DRIVER = r"""
import io, signal, sys
from pathlib import Path
from rtfmup.execute.runner import run_step
from rtfmup.plan.model import Step
signal.signal(signal.SIGINT, signal.default_int_handler)  # as `up` does (cli.py)
child = sys.argv[2]
code = (
    "import subprocess, sys, time\n"
    "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    f"open({child!r}, 'w').write(str(p.pid))\n"
    "time.sleep(60)\n"
)
step = Step(id="s", kind="run", title="t", argv=[sys.executable, "-c", code])
res = run_step(step, Path(sys.argv[1]), out=io.StringIO(), err=io.StringIO(), timeout=None)
print(res.status, flush=True)
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a killed orphan is a zombie when PID 1 does not reap (CI containers)
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True  # no /proc (macOS)


@UNIX
def test_sigint_to_the_process_group_leaves_no_child_behind(tmp_path):
    """Ctrl+C in a terminal signals the foreground process group: rtfmup must stop
    the step's own session (its tree is in a new one) and exit, orphaning nothing."""
    pidfile = tmp_path / "grandchild.pid"
    driver = subprocess.Popen(
        [sys.executable, "-c", DRIVER, str(tmp_path), str(pidfile)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,  # its own group, as a terminal job would have
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (
            pidfile.exists() and pidfile.read_text()
        ):
            time.sleep(0.1)
        grandchild = int(pidfile.read_text())
        os.killpg(driver.pid, signal.SIGINT)  # what the terminal does on Ctrl+C
        stdout, _ = driver.communicate(timeout=30)
    finally:
        if driver.poll() is None:
            os.killpg(driver.pid, signal.SIGKILL)
    assert stdout.strip() == "cancelled"
    end = time.monotonic() + 10
    while time.monotonic() < end and _alive(grandchild):
        time.sleep(0.2)
    assert not _alive(grandchild)
