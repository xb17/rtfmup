"""Run and health (T3.5): start the long-running step, wait for its port, report Up.

Port-in-use is checked before anything starts. The owning PID/name comes from
`Get-NetTCPConnection` (verified on Windows 11); other hosts get the plain gap.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from rtfmup.execute.runner import RunLog, StepResult, run_step
from rtfmup.models import Port
from rtfmup.plan.model import Gap, Step

DEFAULT_HEALTH_TIMEOUT = 120  # seconds
_INTERVAL = 0.25
_LOOPBACK = ("127.0.0.1", "::1")

_OWNER_SCRIPT = (
    "$c = Get-NetTCPConnection -LocalPort @PORT@ -State Listen "
    "-ErrorAction SilentlyContinue | Select-Object -First 1; "
    "if ($c) { $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; "
    '"$($c.OwningProcess)|$($p.ProcessName)" }'
)


@dataclass
class PortOwner:
    pid: int
    name: str | None


@dataclass
class HealthResult:
    up: bool
    url: str
    reason: str = ""
    http_status: int | None = None  # None: port open but no HTTP answer
    elapsed: float = 0.0


def default_url(port: int) -> str:
    return f"http://localhost:{port}"


def port_open(port: int, *, timeout: float = 0.3) -> bool:
    """True if something accepts TCP connections on `port` (IPv4 or IPv6 loopback)."""
    for host in _LOOPBACK:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                if s.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def port_owner(port: int) -> PortOwner | None:
    """Who is listening on `port`. Windows only; None when unknown or free."""
    if sys.platform != "win32":
        return None
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _OWNER_SCRIPT.replace("@PORT@", str(int(port))),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    pid, _, name = line.partition("|")
    if not pid.isdigit():
        return None
    return PortOwner(int(pid), name.strip() or None)


def check_port_free(port: int) -> Gap | None:
    """None if the port is free, else a Gap naming the owner when it can be found."""
    if not port_open(port):
        return None
    owner = port_owner(port)
    who = ""
    if owner:
        who = f" by PID {owner.pid}" + (f" ({owner.name})" if owner.name else "")
    return Gap(
        kind="unresolved",
        message=f"Port {port} is already in use{who}.",
        fix=(
            f"Stop whatever is using port {port}"
            + (f" (PID {owner.pid})" if owner else "")
            + ", or run the project on another port."
        ),
    )


def http_probe(url: str, *, timeout: float = 3.0) -> int | None:
    """Status code of GET `url`, or None if nothing HTTP answered."""
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code  # a 404 still proves an HTTP server is there
    except (OSError, ValueError):
        return None


def wait_until_up(
    port: int,
    *,
    url: str | None = None,
    timeout: float = DEFAULT_HEALTH_TIMEOUT,
    interval: float = _INTERVAL,
    stop: threading.Event | None = None,
) -> HealthResult:
    """Poll TCP until `port` opens, then try HTTP GET. `stop` ends the wait early."""
    url = url or default_url(port)
    stop = stop or threading.Event()
    started = time.monotonic()
    while True:
        elapsed = time.monotonic() - started
        if port_open(port):
            status = http_probe(url)
            return HealthResult(True, url, http_status=status, elapsed=elapsed)
        if stop.is_set():
            return HealthResult(
                False, url, f"process exited before port {port} opened", elapsed=elapsed
            )
        if elapsed >= timeout:
            return HealthResult(
                False, url, f"not up after {timeout:g}s (port {port})", elapsed=elapsed
            )
        stop.wait(interval)


def run_with_health(
    step: Step,
    repo_root: Path,
    port: Port | None,
    *,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    index: int = 1,
    total: int = 1,
    env: Mapping[str, str] | None = None,
    log: RunLog | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[StepResult, HealthResult | None]:
    """Run a long-running step in the foreground with a health watcher beside it.

    Returns when the step ends (Ctrl+C, a cancel, a crash, or a health timeout,
    which stops the process). The HealthResult is None when there is no port.
    """
    out = out or sys.stdout
    err = err or sys.stderr
    if port is None:
        return (
            run_step(
                step,
                repo_root,
                index=index,
                total=total,
                timeout=None,
                env=env,
                log=log,
                out=out,
                err=err,
                cancel=cancel,
            ),
            None,
        )

    url = port.url or default_url(port.port)
    gap = check_port_free(port.port)
    if gap:
        err.write(f"error: {gap.message}\n")
        if gap.fix:
            err.write(f"  fix: {gap.fix}\n")
        err.flush()
        if log:
            log.write(f"step={step.id} status=error port_in_use={port.port}")
        result = StepResult(step.id, "error", None, 0.0)
        return result, HealthResult(False, url, gap.message)

    inner = threading.Event()  # what run_step watches
    exited = threading.Event()  # set once the process is gone
    outcome: list[HealthResult] = []

    def forward_cancel() -> None:
        while not exited.is_set() and not inner.is_set():
            if cancel is not None and cancel.is_set():
                inner.set()
            exited.wait(0.1)

    def watch() -> None:
        res = wait_until_up(
            port.port, url=url, timeout=health_timeout, stop=_Either(exited, inner)
        )
        outcome.append(res)
        if res.up:
            note = f" (HTTP {res.http_status})" if res.http_status else " (port open)"
            err.write(f"Up: {res.url}{note} in {res.elapsed:.1f}s. Ctrl+C to stop.\n")
        elif not exited.is_set() and not inner.is_set():
            err.write(f"error: {res.reason}; stopping it.\n")
            inner.set()  # a server that never listens is a failed run
        err.flush()
        if log:
            log.write(f"step={step.id} health up={res.up} {res.reason}".rstrip())

    threads = [
        threading.Thread(target=forward_cancel, daemon=True),
        threading.Thread(target=watch, daemon=True),
    ]
    for t in threads:
        t.start()
    try:
        result = run_step(
            step,
            repo_root,
            index=index,
            total=total,
            timeout=None,
            env=env,
            log=log,
            out=out,
            err=err,
            cancel=inner,
        )
    finally:
        exited.set()
        for t in threads:
            t.join(timeout=5)
    return result, (outcome[0] if outcome else None)


class _Either:
    """Event-like: set when any of the wrapped events is set."""

    def __init__(self, *events: threading.Event) -> None:
        self._events = events

    def is_set(self) -> bool:
        return any(e.is_set() for e in self._events)

    def wait(self, timeout: float) -> None:
        # Wake on the first event; cheap slicing keeps this dependency-free.
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.is_set():
            time.sleep(min(0.02, timeout))
