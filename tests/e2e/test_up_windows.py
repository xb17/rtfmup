"""T3.6 accept check: `rtfmup up` on the py-http fixture reaches Up (Windows only).

Opt in with `uv run pytest -m e2e`. Runs `uv sync` and a local server inside a
copy of the synthetic fixture; no third-party repo is involved.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "py-http"
pytestmark = pytest.mark.e2e


def test_up_stops_cleanly_off_windows(tmp_path):
    """D7: until M5, other hosts must stop with a clear message, running nothing."""
    if sys.platform == "win32":
        pytest.skip("covered by the Windows test")
    repo = tmp_path / "py-http"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns(".rtfmup", ".venv"))
    proc = subprocess.run(
        [sys.executable, "-m", "rtfmup.cli", "up", str(repo), "--yes"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "not yet supported on this OS" in proc.stderr
    assert not (repo / ".venv").exists() and not (repo / ".env").exists()


def test_up_reaches_up_on_windows(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows only until M5")
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
        pytest.skip("Windows only until M5")
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
