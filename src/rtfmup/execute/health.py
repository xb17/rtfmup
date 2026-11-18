"""Run and health (T3.5): start the long-running step, wait for its port, report Up.

Port-in-use is checked before anything starts. The owning PID/name comes from
`Get-NetTCPConnection` on Windows (verified on Windows 11). On Linux and macOS (T5.5):

  1. `lsof -nP -iTCP:<port> -sTCP:LISTEN -Fpc`: `-n`/`-P` skip name lookups, `-iTCP:port`
     selects the port, `-sTCP:LISTEN` the state, `-F pc` prints `p<pid>` and
     `c<command>` lines (lsof manual, lsof.readthedocs.io, checked 2026-11-18).
  2. Linux only, when lsof is missing: `ss -ltnpH 'sport = :<port>'` (ss(8): -l
     listening, -t tcp, -n numeric, -p processes, -H no header, `sport = :PORT`);
     the `users:(("name",pid=N,fd=N))` field is ss's usual format.
  3. Linux only, when neither tool exists (a slim container): the kernel's own
     tables, `/proc/net/tcp{,6}` for the listening socket's inode and
     `/proc/<pid>/fd/*` for the process holding it. Not in the plan; added because
     the Gitea container has neither tool to rely on. It sees only processes this
     user may inspect, which includes everything rtfmup itself would collide with.

A tool that finds nothing or fails simply yields no owner: the gap is still raised,
just without a PID.
"""

from __future__ import annotations

import os
import re
import shutil
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


def _run_text(argv: list[str]) -> str | None:
    exe = shutil.which(argv[0])
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, *argv[1:]],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def parse_lsof(text: str) -> PortOwner | None:
    """`-F pc` output: `p<pid>` then `c<command>` per process, one field per line."""
    pid: int | None = None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("c") and pid is not None:
            return PortOwner(pid, line[1:] or None)
    return PortOwner(pid, None) if pid is not None else None


_SS_USERS = re.compile(r'\(\("([^"]*)",pid=(\d+)')


def parse_ss(text: str) -> PortOwner | None:
    m = _SS_USERS.search(text)
    return PortOwner(int(m.group(2)), m.group(1) or None) if m else None


def _proc_owner(port: int, proc: Path = Path("/proc")) -> PortOwner | None:
    """The process holding the listening socket on `port`, from /proc."""
    inodes: set[str] = set()
    for table in ("net/tcp", "net/tcp6"):
        try:
            rows = (proc / table).read_text(encoding="ascii").splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            # sl local_address rem_address st ... inode; st 0A is LISTEN
            listening = len(cols) > 9 and cols[3] == "0A"
            if listening and int(cols[1].rpartition(":")[2], 16) == port:
                inodes.add(cols[9])
    if not inodes:
        return None
    wanted = {f"socket:[{i}]" for i in inodes}
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    if os.readlink(fd) in wanted:
                        try:
                            name = (entry / "comm").read_text().strip() or None
                        except OSError:
                            name = None
                        return PortOwner(int(entry.name), name)
                except OSError:
                    continue
        except OSError:
            continue  # not ours to look at
    return None


def _port_owner_posix(port: int) -> PortOwner | None:
    port = int(port)
    text = _run_text(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fpc"])
    if text and (owner := parse_lsof(text)):
        return owner
    if sys.platform.startswith("linux"):
        text = _run_text(["ss", "-ltnpH", f"sport = :{port}"])
        if text and (owner := parse_ss(text)):
            return owner
        return _proc_owner(port)
    return None


def port_owner(port: int) -> PortOwner | None:
    """Who is listening on `port`; None when unknown or free."""
    if sys.platform != "win32":
        return _port_owner_posix(port)
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
    quiet: bool = False,
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
                quiet=quiet,
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
            quiet=quiet,
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
