"""T3.6 / T5.1 accept checks: `rtfmup up` on the py-http fixture reaches Up.

Opt in with `uv run pytest -m e2e`. Runs `uv sync` and a local server inside a
copy of the synthetic fixture; no third-party repo is involved.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "py-http"
pytestmark = pytest.mark.e2e


def _up_until_ready(repo: Path, **popen_kw) -> tuple[subprocess.Popen, list[str], bool]:
    proc = subprocess.Popen(
        [sys.executable, "-m", "rtfmup.cli", "up", str(repo), "--yes"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        **popen_kw,
    )
    lines: list[str] = []
    deadline = time.monotonic() + 240
    for line in proc.stdout:
        lines.append(line)
        if "Up: http://localhost:8000" in line:
            return proc, lines, True
        if time.monotonic() > deadline:
            break
    return proc, lines, False


def _port_closed(port: int, wait: float = 10.0) -> bool:
    end = time.monotonic() + wait
    while time.monotonic() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return True
        time.sleep(0.2)
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; Windows below")
def test_up_reaches_up_on_linux_and_macos_and_sigint_leaves_nothing_behind(tmp_path):
    """T5.1 accept: `up` on py-http reaches `Up:`; SIGINT to its process group (what
    Ctrl+C sends) stops rtfmup and the server it started."""
    repo = tmp_path / "py-http"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns(".rtfmup", ".venv"))
    proc, lines, up = _up_until_ready(repo, start_new_session=True)
    try:
        assert up, "never reached Up:\n" + "".join(lines)
        os.killpg(proc.pid, signal.SIGINT)
        rest, _ = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
    assert proc.returncode == 0, "".join(lines) + rest
    assert _port_closed(8000), "the server outlived rtfmup"


def test_up_reaches_up_on_windows(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows variant")
    repo = tmp_path / "py-http"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns(".rtfmup", ".venv"))
    proc = subprocess.Popen(
        [sys.executable, "-m", "rtfmup.cli", "up", str(repo), "--yes"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    lines: list[str] = []
    up = False
    deadline = time.monotonic() + 240
    try:
        for line in proc.stdout:
            lines.append(line)
            if "Up: http://localhost:8000" in line:
                up = True
                break
            if time.monotonic() > deadline:
                break
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False
                )
    assert up, "never reached Up:\n" + "".join(lines)


def test_up_prints_a_diagnosis_for_a_failing_step(tmp_path):
    """T4.1 accept: the MSVC failure fixture exits 1 with the explanation and fix."""
    if sys.platform != "win32":
        pytest.skip("Windows variant")
    repo = tmp_path / "diagnose-msvc"
    shutil.copytree(
        FIXTURE.parent / "diagnose-msvc", repo, ignore=shutil.ignore_patterns(".rtfmup")
    )
    proc = subprocess.run(
        [sys.executable, "-m", "rtfmup.cli", "up", str(repo), "--yes"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    err = proc.stderr
    assert proc.returncode == 1, err
    assert err.index("FAILED") < err.index("Diagnosis:")
    assert "Build Tools" in err and "fix:" in err


def test_up_resume_continues_after_the_failed_step(tmp_path):
    """T4.2 accept: step 1 shows as already done and the second run exits 0."""
    repo = tmp_path / "resume"
    shutil.copytree(
        FIXTURE.parent / "resume", repo, ignore=shutil.ignore_patterns(".rtfmup")
    )

    def up(*extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "rtfmup.cli", "up", str(repo), "--yes", *extra],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )

    assert up().returncode == 1
    (repo / ".ok").write_text("")
    done = up("--resume")
    assert done.returncode == 0, done.stderr
    assert "already done" in done.stderr


def _diagnosis_after_failure(tmp_path, fixture: str):
    repo = tmp_path / fixture
    shutil.copytree(
        FIXTURE.parent / fixture, repo, ignore=shutil.ignore_patterns(".rtfmup")
    )
    proc = subprocess.run(
        [sys.executable, "-m", "rtfmup.cli", "up", str(repo), "--yes"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    return proc


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS diagnosis (T5.6)")
def test_up_prints_a_macos_diagnosis_for_a_failing_step(tmp_path):
    """T5.6 accept (macOS): the Command Line Tools failure fixture exits 1 and prints
    the explanation and fix after the failure line."""
    proc = _diagnosis_after_failure(tmp_path, "diagnose-xcode")
    err = proc.stderr
    assert proc.returncode == 1, err
    assert err.index("FAILED") < err.index("Diagnosis:")
    assert "Command Line Tools" in err and "xcode-select --install" in err


@pytest.mark.skipif(sys.platform != "linux", reason="Linux diagnosis (T5.6)")
def test_up_prints_a_linux_diagnosis_for_a_failing_step(tmp_path):
    """T5.6 accept (Linux): the PEP 668 failure fixture exits 1 and prints the
    explanation and fix after the failure line."""
    proc = _diagnosis_after_failure(tmp_path, "diagnose-pep668")
    err = proc.stderr
    assert proc.returncode == 1, err
    assert err.index("FAILED") < err.index("Diagnosis:")
    assert "PEP 668" in err and "uv venv" in err
