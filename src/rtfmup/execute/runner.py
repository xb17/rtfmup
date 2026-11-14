"""Step runner (T3.1): live streaming, timeouts, cancellation, redacted log."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Literal

from rtfmup.execute.redact import Redactor
from rtfmup.execute.shells import CommandError, build_command, display_command
from rtfmup.plan.model import Step

Status = Literal["ok", "failed", "timeout", "cancelled", "error"]

DEFAULT_TIMEOUT = 30 * 60  # seconds, for non-run steps. `run` steps pass None.
_GRACE = 5.0  # seconds between the polite stop and the forced tree kill
_POLL = 0.1

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


def _pump(src: IO[str], sink: IO[str], lock: threading.Lock) -> None:
    for line in src:
        with lock:
            sink.write(line)
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
) -> StepResult:
    """Run one step and stream its output live.

    `env` is merged over the process environment and the step's own env. It is
    where secret values from `.env` go; they are masked in our own messages and
    in `run.log`. `timeout=None` means no limit.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    cancel = cancel or threading.Event()
    child_env = {**os.environ, **step.env, **(env or {})}
    redact = Redactor(child_env)

    def say(msg: str) -> None:
        err.write(redact(msg) + "\n")
        err.flush()

    say(step_header(index, total, step))
    started = time.monotonic()

    def finish(status: Status, code: int | None) -> StepResult:
        result = StepResult(step.id, status, code, time.monotonic() - started)
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
        argv = build_command(step)
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
        threading.Thread(target=_pump, args=(proc.stdout, out, lock), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err, lock), daemon=True),
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
