"""T3.5 run-and-health tests. Servers are stdlib http.server children on free ports."""

from __future__ import annotations

import io
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from rtfmup.execute import health
from rtfmup.execute.health import (
    check_port_free,
    port_open,
    port_owner,
    run_with_health,
    wait_until_up,
)
from rtfmup.models import Port
from rtfmup.plan.model import Step

SERVER = (
    "import http.server, sys\n"
    "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])),"
    " http.server.SimpleHTTPRequestHandler).serve_forever()"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def listener() -> tuple[socket.socket, int]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def server_step(port: int, code: str = SERVER) -> Step:
    return Step(
        id="run",
        kind="run",
        title="serve",
        argv=[sys.executable, "-c", code, str(port)],
        long_running=True,
    )


def port_model(port: int, url: str | None = None) -> Port:
    return Port(port=port, url=url, evidence=[])


def test_port_open_reflects_listener():
    s, port = listener()
    try:
        assert port_open(port)
    finally:
        s.close()
    assert not port_open(port)


def test_check_port_free_returns_none_when_free():
    assert check_port_free(free_port()) is None


def test_check_port_free_gap_when_in_use():
    s, port = listener()
    try:
        gap = check_port_free(port)
    finally:
        s.close()
    assert gap is not None
    assert gap.kind == "unresolved"
    assert str(port) in gap.message and "in use" in gap.message


@pytest.mark.skipif(sys.platform != "win32", reason="Get-NetTCPConnection is Windows")
def test_port_owner_finds_our_own_pid():
    s, port = listener()
    try:
        owner = port_owner(port)
    finally:
        s.close()
    assert owner is not None and owner.pid == os.getpid()
    assert owner.name and "python" in owner.name.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="Get-NetTCPConnection is Windows")
def test_gap_names_owning_process():
    s, port = listener()
    try:
        gap = check_port_free(port)
    finally:
        s.close()
    assert gap is not None and f"PID {os.getpid()}" in gap.message


def test_port_owner_none_when_nobody_listens():
    assert port_owner(free_port()) is None


def test_wait_until_up_times_out():
    result = wait_until_up(free_port(), timeout=0.5, interval=0.05)
    assert not result.up
    assert "not up after" in result.reason


def test_wait_until_up_stops_when_told():
    stop = threading.Event()
    stop.set()
    result = wait_until_up(free_port(), timeout=30, interval=0.05, stop=stop)
    assert not result.up and "exited" in result.reason


def test_wait_until_up_sees_late_listener():
    port = free_port()
    holder: list[socket.socket] = []

    def later():
        time.sleep(0.3)
        s = socket.socket()
        s.bind(("127.0.0.1", port))
        s.listen()
        holder.append(s)

    threading.Thread(target=later, daemon=True).start()
    result = wait_until_up(port, timeout=10, interval=0.05)
    for s in holder:
        s.close()
    assert result.up and result.url == f"http://localhost:{port}"
    assert result.http_status is None  # raw listener: open but not HTTP


def test_port_url_wins_over_default():
    s, port = listener()
    try:
        r = wait_until_up(port, url="http://localhost:9/app", timeout=2, interval=0.05)
    finally:
        s.close()
    assert r.up and r.url == "http://localhost:9/app"


def test_run_with_health_reports_up_then_cancel(tmp_path: Path):
    port = free_port()
    cancel = threading.Event()
    out, err = io.StringIO(), io.StringIO()

    def stop_when_up():
        for _ in range(200):
            if "Up:" in err.getvalue():
                break
            time.sleep(0.05)
        cancel.set()

    threading.Thread(target=stop_when_up, daemon=True).start()
    result, hr = run_with_health(
        server_step(port),
        tmp_path,
        port_model(port),
        health_timeout=30,
        out=out,
        err=err,
        cancel=cancel,
    )
    assert hr is not None and hr.up
    assert hr.http_status == 200
    assert f"Up: http://localhost:{port}" in err.getvalue()
    assert result.status == "cancelled"
    assert not port_open(port)  # child tree was stopped


def test_run_with_health_refuses_busy_port(tmp_path: Path):
    s, port = listener()
    err = io.StringIO()
    try:
        result, hr = run_with_health(
            server_step(port), tmp_path, port_model(port), out=io.StringIO(), err=err
        )
    finally:
        s.close()
    assert result.status == "error" and result.exit_code is None
    assert hr is not None and not hr.up and "in use" in hr.reason
    assert "in use" in err.getvalue()


def test_run_with_health_server_that_never_listens(tmp_path: Path):
    port = free_port()
    err = io.StringIO()
    result, hr = run_with_health(
        server_step(port, "import time; time.sleep(60)"),
        tmp_path,
        port_model(port),
        health_timeout=1,
        out=io.StringIO(),
        err=err,
    )
    assert hr is not None and not hr.up and "not up after" in hr.reason
    assert result.status == "cancelled"  # we stopped it
    assert "not up after" in err.getvalue()


def test_run_with_health_process_exits_early(tmp_path: Path):
    port = free_port()
    result, hr = run_with_health(
        server_step(port, "raise SystemExit(3)"),
        tmp_path,
        port_model(port),
        health_timeout=30,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert result.status == "failed" and result.exit_code == 3
    assert hr is not None and not hr.up and "exited" in hr.reason


def test_no_port_means_plain_run(tmp_path: Path):
    step = Step(
        id="run", kind="run", title="t", argv=[sys.executable, "-c", "print('hi')"]
    )
    out = io.StringIO()
    result, hr = run_with_health(step, tmp_path, None, out=out, err=io.StringIO())
    assert result.ok and hr is None and out.getvalue().strip() == "hi"


def test_module_exports():
    assert health.DEFAULT_HEALTH_TIMEOUT == 120
