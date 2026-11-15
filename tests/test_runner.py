"""T3.1 runner tests. Steps are python one-liners; nothing touches the host."""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path

from rtfmup.execute import runner
from rtfmup.execute.redact import Redactor
from rtfmup.execute.runner import RunLog, run_step, step_header
from rtfmup.plan.model import Step


def py(code: str, **kw) -> Step:
    return Step(
        id=kw.pop("id", "s1"),
        kind=kw.pop("kind", "setup"),
        title="t",
        argv=[sys.executable, "-c", code],
        **kw,
    )


def run(step: Step, tmp_path: Path, **kw):
    out, err = io.StringIO(), io.StringIO()
    result = run_step(step, tmp_path, out=out, err=err, **kw)
    return result, out.getvalue(), err.getvalue()


def test_success_streams_stdout_in_order(tmp_path):
    result, out, err = run(py("print('a'); print('b'); print('c')"), tmp_path)
    assert result.ok and result.exit_code == 0
    assert out.splitlines() == ["a", "b", "c"]
    assert "done (exit 0)" in err


def test_stderr_goes_to_err_stream(tmp_path):
    _, out, err = run(py("import sys; print('x', file=sys.stderr)"), tmp_path)
    assert out == ""
    assert "x" in err.splitlines()


def test_exit_code_propagates(tmp_path):
    result, _, err = run(py("raise SystemExit(7)"), tmp_path)
    assert result.status == "failed" and result.exit_code == 7
    assert "FAILED (exit 7)" in err


def test_output_is_live_not_buffered_until_exit(tmp_path):
    """The first line must arrive while the child is still running."""
    seen = threading.Event()

    class Sink(io.StringIO):
        def write(self, s):
            if "first" in s:
                seen.set()
            return super().write(s)

    code = "import time; print('first', flush=True); time.sleep(1.5); print('last')"
    done = threading.Event()
    res: list = []

    def target():
        res.append(run_step(py(code), tmp_path, out=Sink(), err=io.StringIO()))
        done.set()

    threading.Thread(target=target).start()
    assert seen.wait(1.2)
    assert not done.is_set()
    assert done.wait(10)
    assert res[0].ok


def test_cwd_is_repo_relative(tmp_path):
    (tmp_path / "web").mkdir()
    _, out, _ = run(py("import os; print(os.getcwd())", cwd="web"), tmp_path)
    assert Path(out.strip()) == (tmp_path / "web").resolve()


def test_step_env_and_extra_env_reach_child(tmp_path):
    step = py("import os; print(os.environ['A'], os.environ['B'])", env={"A": "1"})
    _, out, _ = run(step, tmp_path, env={"B": "2"})
    assert out.split() == ["1", "2"]


def test_timeout_kills_child(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_GRACE", 1.0)
    started = time.monotonic()
    result, _, err = run(py("import time; time.sleep(60)"), tmp_path, timeout=0.5)
    assert result.status == "timeout"
    assert time.monotonic() - started < 15
    assert "TIMED OUT" in err


def test_cancel_kills_process_tree(tmp_path, monkeypatch):
    """A grandchild must die with its parent, not linger after Ctrl+C."""
    monkeypatch.setattr(runner, "_GRACE", 1.0)
    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    cancel = threading.Event()

    def trigger():
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text():
                break
            time.sleep(0.1)
        cancel.set()

    threading.Thread(target=trigger).start()
    result, _, _ = run(py(code), tmp_path, cancel=cancel)
    assert result.status == "cancelled"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    assert not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import subprocess

        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return str(pid) in out
    import os

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A killed orphan is a zombie until something reaps it, and a container whose
    # PID 1 doesn't reap (the Gitea runner) keeps it forever. os.kill() still
    # succeeds on a zombie, but it is not running.
    try:
        with open(f"/proc/{pid}/stat", encoding="ascii") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True  # no /proc (macOS): os.kill() above is the answer


def test_missing_program_is_an_error_not_a_crash(tmp_path):
    step = Step(id="s", kind="setup", title="t", argv=["definitely-not-a-program-xyz"])
    result, _, err = run(step, tmp_path)
    assert result.status == "error" and result.exit_code is None
    assert "could not start" in err


def test_step_without_command_is_an_error(tmp_path):
    result, _, err = run(Step(id="s", kind="manual", title="t"), tmp_path)
    assert result.status == "error"
    assert "neither argv nor shell_text" in err


def test_header_format():
    step = Step(id="s", kind="install", title="t", argv=["npm", "ci"], cwd="web")
    assert step_header(3, 7, step) == "[3/7] install · npm ci · cwd=web"
    step = Step(id="s", kind="install", title="t", argv=["npm", "ci"])
    assert step_header(1, 2, step) == "[1/2] install · npm ci"


# --- redaction -------------------------------------------------------------


def test_redactor_masks_only_secret_like_names():
    r = Redactor({"API_TOKEN": "hunter2xyz", "PORT": "8080", "SHORT_KEY": "ab"})
    assert r("t=hunter2xyz p=8080 k=ab") == "t=*** p=8080 k=ab"


def test_redactor_masks_longest_value_first():
    r = Redactor({"A_SECRET": "abc", "B_SECRET": "abcdef"})
    assert r("abcdef") == "***"


def test_log_and_messages_redact_secret_values(tmp_path):
    log = RunLog(tmp_path / ".rtfmup" / "run.log")
    secret = "s3cr3t-value-123"
    step = py("print('ok')")
    step.argv.append(secret)  # a secret ending up on the command line
    result, _out, err = run(step, tmp_path, env={"DB_PASSWORD": secret}, log=log)
    text = log.path.read_text(encoding="utf-8")
    assert secret not in text and secret not in err
    assert "***" in text
    assert "status=ok exit=0" in text
    assert result.ok


def test_log_records_command_exit_code_and_timing(tmp_path):
    log = RunLog(tmp_path / "run.log")
    run(py("raise SystemExit(3)", id="boom"), tmp_path, log=log)
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert any("step=boom" in ln and "cmd=" in ln for ln in lines)
    assert any(
        "status=failed exit=3" in ln and ln.rstrip().endswith("s") for ln in lines
    )


def test_own_virtualenv_is_not_inherited(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "C:/rtfmup/.venv")
    code = "import os; print(os.environ.get('VIRTUAL_ENV', 'unset'))"
    _, out, _ = run(py(code), tmp_path)
    assert out.strip() == "unset"
    _, out, _ = run(py(code, env={"VIRTUAL_ENV": "mine"}), tmp_path)
    assert out.strip() == "mine"


class _Latin1Sink(io.StringIO):
    """A console that cannot encode everything, like cp1252 on Windows."""

    def write(self, s):
        s.encode("cp1252")
        return super().write(s)


def test_output_the_console_cannot_encode_does_not_kill_the_stream(tmp_path):
    code = (
        "import sys; sys.stdout.buffer.write("
        "'ok '.encode() + bytes([0xE2, 0x9C, 0x93]) + ' done'.encode() + bytes([10]))"
    )
    out, err = _Latin1Sink(), io.StringIO()
    result = run_step(py(code), tmp_path, out=out, err=err)
    assert result.status == "ok"
    assert "done" in out.getvalue()
