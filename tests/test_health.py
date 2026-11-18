"""T3.5 run-and-health tests. Servers are stdlib http.server children on free ports."""

from __future__ import annotations

import io
import os
import socket
import sys
import threading
import time
from pathlib import Path

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
    # TCPServer, not HTTPServer: HTTPServer.server_bind calls socket.getfqdn(),
    # a reverse-DNS lookup that stalls for tens of seconds on macOS CI runners.
    "import http.server, socketserver, sys\n"
    "socketserver.TCPServer(('127.0.0.1', int(sys.argv[1])),"
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


def test_port_owner_finds_our_own_pid():
    """Windows: Get-NetTCPConnection. Linux/macOS (T5.5): lsof, then ss, then /proc."""
    s, port = listener()
    try:
        owner = port_owner(port)
    finally:
        s.close()
    assert owner is not None and owner.pid == os.getpid()
    assert owner.name
    if sys.platform == "win32":
        # elsewhere the name is the kernel's: a pytest console script reports "pytest"
        assert "python" in owner.name.lower()


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


# --- T5.5: parsers and the /proc fallback, with captured tool output ------------------


def test_parse_lsof_field_output():
    from rtfmup.execute.health import parse_lsof

    assert parse_lsof("p4242\ncpython3.12\n") == health.PortOwner(4242, "python3.12")
    # one process with several fds: the first process wins
    assert parse_lsof("p7\ncnode\nf20\nf21\np8\ncother\n").pid == 7
    assert parse_lsof("p9\n") == health.PortOwner(9, None)
    assert parse_lsof("") is None and parse_lsof("garbage\n") is None


def test_parse_ss_users_field():
    from rtfmup.execute.health import parse_ss

    line = 'LISTEN 0 5 127.0.0.1:8000 0.0.0.0:* users:(("python3",pid=1234,fd=3))\n'
    assert parse_ss(line) == health.PortOwner(1234, "python3")
    two = 'LISTEN 0 4096 *:80 *:* users:(("nginx",pid=11,fd=6),("nginx",pid=12,fd=6))'
    assert parse_ss(two).pid == 11
    assert parse_ss("LISTEN 0 5 127.0.0.1:8000 0.0.0.0:*\n") is None  # not our uid


def test_proc_fallback_finds_the_holder_of_a_listening_inode(tmp_path, monkeypatch):
    (tmp_path / "net").mkdir()
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
    (tmp_path / "net" / "tcp").write_text(
        header
        + "   0: 0100007F:1F40 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000 0 99001 1 0 100 0 0 10 0\n"
        + "   1: 0100007F:1F41 00000000:0000 01 00000000:00000000 00:00000000 00000000  1000 0 99002 1 0 100 0 0 10 0\n",
        encoding="ascii",
    )
    (tmp_path / "net" / "tcp6").write_text(header, encoding="ascii")
    sockets = {"100": "socket:[11]", "4242": "socket:[99001]"}
    for pid in sockets:
        fd = tmp_path / pid / "fd"
        fd.mkdir(parents=True)
        (fd / "3").write_text("")
        (tmp_path / pid / "comm").write_text("holder\n" if pid == "4242" else "other\n")
    monkeypatch.setattr(
        health.os, "readlink", lambda p: sockets[Path(p).parent.parent.name]
    )
    (tmp_path / "self").mkdir()  # non-numeric entries are skipped
    assert health._proc_owner(8000, tmp_path) == health.PortOwner(4242, "holder")
    assert (
        health._proc_owner(8001, tmp_path) is None
    )  # 0x1F41 is ESTABLISHED, not LISTEN
    assert health._proc_owner(9, tmp_path) is None


def test_a_missing_tool_means_no_owner_not_an_error(monkeypatch):
    monkeypatch.setattr(health.shutil, "which", lambda _n: None)
    assert health._run_text(["lsof", "-v"]) is None
