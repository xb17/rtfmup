"""Step runner (T3.1): live streaming, timeouts, cancellation, redacted log."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Literal

from rtfmup.execute.redact import Redactor
from rtfmup.execute.shells import CommandError, build_command, display_command
from rtfmup.plan.model import Step

Status = Literal["ok", "failed", "timeout", "cancelled", "error"]

DEFAULT_TIMEOUT = 30 * 60  # seconds, for non-run steps. `run` steps pass None.
TAIL_LINES = 200  # output kept per step, for diagnostics (T4.1) and --quiet (T4.3)
_GRACE = 5.0  # seconds between the polite stop and the forced tree kill
_POLL = 0.1
# rtfmup's own interpreter environment must not steer the target repo's tools
# (uv warns and ignores a VIRTUAL_ENV that is not the project's). A step can
# still set these explicitly.
_OWN_PYTHON_ENV = ("VIRTUAL_ENV", "PYTHONHOME")
VENV_BIN = "Scripts" if sys.platform == "win32" else "bin"


def project_venv(repo_root: Path, cwd: str) -> Path | None:
    """The project's own virtualenv for a step in `cwd`, if one exists.

    Checked per step because an earlier step (`uv sync`, `uv venv`) creates it.
    A `.venv` tracked by git is repo content, and repo content is untrusted: putting
    its bin directory first on PATH would let the repo choose which `npm` runs.
    """
    for base in dict.fromkeys([(repo_root / cwd).resolve(), repo_root.resolve()]):
        for name in (".venv", "venv"):  # uv's default, then the venv docs' usual name
            venv = base / name
            if (venv / "pyvenv.cfg").is_file() and not _tracked(repo_root, venv):
                return venv
    return None


def _tracked(repo_root: Path, path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


def step_environment(
    repo_root: Path, step: Step, env: Mapping[str, str] | None
) -> dict[str, str]:
    """What a step's process sees: the host env minus rtfmup's own Python, the
    project's venv activated, pip barred from installing outside a venv, then the
    step's env and the caller's (secrets from `.env`, a refreshed PATH) on top."""
    child = {k: v for k, v in os.environ.items() if k not in _OWN_PYTHON_ENV}
    child.update(step.env)
    child.update(env or {})
    # A README `pip install` must never land in a global Python.
    child.setdefault("PIP_REQUIRE_VIRTUALENV", "1")
    venv = project_venv(repo_root, step.cwd)
    if venv is not None and "VIRTUAL_ENV" not in child:
        child["VIRTUAL_ENV"] = str(venv)
        child["PATH"] = str(venv / VENV_BIN) + os.pathsep + child.get("PATH", "")
    return child


_LABEL = {
    "ok": "done",
    "failed": "FAILED",
    "timeout": "TIMED OUT",
    "cancelled": "cancelled",
    "error": "ERROR",
}


@dataclass
class StepResult:
    step_id: str
    status: Status
    exit_code: int | None
    duration: float
    tail: list[str] = field(default_factory=list)  # last TAIL_LINES lines

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class RunLog:
    """Append-only `run.log`: commands, exit codes, timings. Always redacted."""

    def __init__(self, path: Path, redact: Redactor | None = None) -> None:
        self.path = path
        self._redact = redact or Redactor()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, message: str) -> None:
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {self._redact(message)}\n")


def step_header(index: int, total: int, step: Step) -> str:
    """`[3/7] install · npm ci · cwd=web`"""
    cwd = f" · cwd={step.cwd}" if step.cwd not in ("", ".") else ""
    return f"[{index}/{total}] {step.kind} · {display_command(step) or step.title}{cwd}"


def _popen_flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class _Job:
    """Windows job object: terminating it kills the whole tree, even orphans.

    `taskkill /T` walks parent links, which are gone once the leader exits, so a
    grandchild could survive. A job keeps membership regardless.
    """

    def __init__(self, proc: subprocess.Popen) -> None:
        self._handle = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32 = k32
        job = k32.CreateJobObjectW(None, None)
        if job and k32.AssignProcessToJobObject(
            job, wintypes.HANDLE(int(proc._handle))
        ):
            self._handle = job
        elif job:
            k32.CloseHandle(job)

    def terminate(self) -> bool:
        if self._handle is None:
            return False
        self._k32.TerminateJobObject(self._handle, 1)
        return True

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None


def _alive(proc: subprocess.Popen) -> bool:
    return proc.poll() is None


def _stop_tree(proc: subprocess.Popen, job: _Job) -> None:
    """Ask the process tree to stop, then force it after a grace period."""
    if not _alive(proc):
        job.terminate()  # leader already gone; reap any orphaned descendants
        return
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.monotonic() + _GRACE
    while _alive(proc) and time.monotonic() < deadline:
        time.sleep(_POLL)
    if sys.platform == "win32":
        if not job.terminate():  # job assignment failed: best effort via parent links
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=_GRACE)
    except subprocess.TimeoutExpired:
        pass


def _pump(
    src: IO[str],
    sink: IO[str] | None,
    lock: threading.Lock,
    tail: deque[str] | None = None,
) -> None:
    for line in src:
        with lock:
            if tail is not None:
                tail.append(line.rstrip("\r\n"))
            if sink is None:
                continue
            try:
                sink.write(line)
            except UnicodeEncodeError:
                # A console codepage (cp1252) can't show everything a tool prints.
                # Degrade the characters, never the stream.
                enc = getattr(sink, "encoding", None) or "ascii"
                sink.write(line.encode(enc, "replace").decode(enc))
            sink.flush()
    src.close()


def run_step(
    step: Step,
    repo_root: Path,
    *,
    index: int = 1,
    total: int = 1,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    log: RunLog | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    cancel: threading.Event | None = None,
    quiet: bool = False,
) -> StepResult:
    """Run one step and stream its output live.

    `env` is merged over the process environment and the step's own env. It is
    where secret values from `.env` go; they are masked in our own messages and
    in `run.log`. `timeout=None` means no limit. `quiet` keeps child output off the
    terminal; the last lines are still in `StepResult.tail`.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    cancel = cancel or threading.Event()
    child_env = step_environment(repo_root, step, env)
    redact = Redactor(child_env)
    captured: deque[str] = deque(maxlen=TAIL_LINES)

    def say(msg: str) -> None:
        err.write(redact(msg) + "\n")
        err.flush()

    say(step_header(index, total, step))
    started = time.monotonic()

    def finish(status: Status, code: int | None) -> StepResult:
        result = StepResult(
            step.id,
            status,
            code,
            time.monotonic() - started,
            [redact(t) for t in captured],
        )
        if log:
            log.write(
                redact(
                    f"step={step.id} status={status} exit={code} {result.duration:.1f}s"
                )
            )
        tail = f" (exit {code})" if code is not None else ""
        say(f"{_LABEL[status]}{tail} in {result.duration:.1f}s")
        return result

    try:
        argv = build_command(step, path=child_env.get("PATH"))
    except CommandError as exc:
        say(f"error: {exc}")
        return finish("error", None)

    if log:
        log.write(redact(f"step={step.id} cwd={step.cwd} cmd={display_command(step)}"))
    try:
        proc = subprocess.Popen(
            argv,
            cwd=(repo_root / step.cwd).resolve(),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **_popen_flags(),
        )
    except OSError as exc:
        say(f"error: could not start: {exc}")
        return finish("error", None)

    job = _Job(proc)
    lock = threading.Lock()
    pumps = [
        threading.Thread(
            target=_pump,
            args=(proc.stdout, None if quiet else out, lock, captured),
            daemon=True,
        ),
        threading.Thread(
            target=_pump,
            args=(proc.stderr, None if quiet else err, lock, captured),
            daemon=True,
        ),
    ]
    for t in pumps:
        t.start()

    status: Status | None = None
    try:
        while _alive(proc):
            if cancel.is_set():
                status = "cancelled"
            elif timeout is not None and time.monotonic() - started > timeout:
                status = "timeout"
            if status:
                _stop_tree(proc, job)
                break
            time.sleep(_POLL)
    except KeyboardInterrupt:
        status = "cancelled"
        _stop_tree(proc, job)
    for t in pumps:
        t.join(timeout=_GRACE)

    job.close()
    code = proc.returncode
    if status is None:
        status = "ok" if code == 0 else "failed"
    return finish(status, code)
