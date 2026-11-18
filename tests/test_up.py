"""T3.6 `up` orchestration tests. Steps are python one-liners in tmp dirs."""

from __future__ import annotations

import io
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from rtfmup.analyze import analyze
from rtfmup.execute.confirm import Confirmer
from rtfmup.execute.envsetup import Prompter
from rtfmup.execute.up import execute_plan
from rtfmup.models import EnvVar, Port, SourceRef
from rtfmup.plan.model import Gap, Plan, Step

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


def py(id_: str, kind: str, code: str, **kw) -> Step:
    return Step(
        id=id_, kind=kind, title=f"{id_} title", argv=[sys.executable, "-c", code], **kw
    )


def make_plan(steps, *, gaps=(), health=None, os_="windows") -> Plan:
    return Plan(
        analysis_ref="x",
        host_os=os_,
        host_arch="x86_64",
        variant=None,
        component="root",
        steps=list(steps),
        gaps=list(gaps),
        health=health,
    )


def touch(path: Path) -> str:
    return f"open({str(path)!r}, 'w').close()"


def go(plan, root, *, answers=(), assume_yes=False, **kw):
    out, err = io.StringIO(), io.StringIO()
    it = iter(answers)
    confirmer = Confirmer(assume_yes=assume_yes, read=lambda _p: next(it), out=err)
    result = execute_plan(
        plan,
        analyze(root),
        root,
        assume_yes=assume_yes,
        interactive=False,
        confirmer=confirmer,
        out=out,
        err=err,
        **kw,
    )
    return result, out.getvalue(), err.getvalue()


def state(root: Path) -> dict:
    return json.loads((root / ".rtfmup" / "state.json").read_text(encoding="utf-8"))


def env_step() -> Step:
    return Step(
        id="e",
        kind="env",
        title="env",
        evidence=[
            SourceRef(file=".env.example", line_start=1, detector="env.template")
        ],
    )


def token_var() -> EnvVar:
    return EnvVar(name="API_TOKEN", required="required", secret_like=True, evidence=[])


@pytest.mark.parametrize("host_os", ["windows", "linux", "macos"])
def test_up_executes_a_plan_on_every_host_os(tmp_path, host_os):
    """T5.1 flips D7's old stop: Linux and macOS plans run like Windows ones."""
    marker = tmp_path / "ran"
    result, _, _ = go(
        make_plan([py("a", "setup", touch(marker))], os_=host_os),
        tmp_path,
        assume_yes=True,
    )
    assert result.outcome == "stopped" and result.exit_code == 0
    assert marker.exists() and state(tmp_path)["steps"]["a"]["status"] == "ok"


def test_blocking_gap_stops_before_anything_runs(tmp_path):
    gap = Gap(kind="missing_runtime", message="node 20 needed", fix="winget install x")
    marker = tmp_path / "ran"
    step = py("a", "setup", touch(marker))
    result, _, err = go(make_plan([step], gaps=[gap]), tmp_path, assume_yes=True)
    assert result.outcome == "blocked" and result.exit_code == 4
    assert "node 20 needed" in err and "winget install x" in err
    assert not marker.exists()


def test_declined_runs_nothing(tmp_path):
    marker = tmp_path / "ran"
    result, _, _ = go(
        make_plan([py("a", "setup", touch(marker))]), tmp_path, answers=["n"]
    )
    assert result.outcome == "declined" and result.exit_code == 3
    assert not marker.exists()


def test_eof_is_a_refusal(tmp_path):
    def eof(_p):
        raise EOFError

    err = io.StringIO()
    result = execute_plan(
        make_plan([py("a", "setup", "pass")]),
        analyze(tmp_path),
        tmp_path,
        confirmer=Confirmer(read=eof, out=err),
        out=io.StringIO(),
        err=err,
    )
    assert result.outcome == "declined"


def test_steps_run_in_order_and_state_is_recorded(tmp_path):
    log = tmp_path / "order.txt"
    steps = [
        py("a", "install", f"open({str(log)!r}, 'a').write('a')"),
        py("b", "setup", f"open({str(log)!r}, 'a').write('b')"),
    ]
    result, _, _ = go(make_plan(steps), tmp_path, answers=["y"])
    assert result.outcome == "stopped"  # no run step
    assert log.read_text() == "ab"
    s = state(tmp_path)
    assert s["outcome"] == "stopped"
    assert [v["status"] for v in s["steps"].values()] == ["ok", "ok"]
    assert (tmp_path / ".rtfmup" / "run.log").is_file()


def test_failure_stops_the_plan(tmp_path):
    marker = tmp_path / "later"
    steps = [
        py("a", "install", "raise SystemExit(5)"),
        py("b", "setup", touch(marker)),
    ]
    result, _, _ = go(make_plan(steps), tmp_path, assume_yes=True)
    assert result.outcome == "failed" and result.exit_code == 1
    assert not marker.exists()
    s = state(tmp_path)["steps"]
    assert s["a"]["status"] == "failed" and s["a"]["exit_code"] == 5
    assert s["b"]["status"] == "pending"


def test_yes_skips_high_risk_steps(tmp_path):
    marker = tmp_path / "ran"
    risky = py("a", "install", touch(marker), risk="high")
    result, _, err = go(
        make_plan([risky, py("b", "setup", "pass")]), tmp_path, assume_yes=True
    )
    assert result.outcome == "stopped"
    assert not marker.exists()
    assert "--yes never approves high-risk" in err
    s = state(tmp_path)["steps"]
    assert s["a"]["status"] == "skipped" and s["b"]["status"] == "ok"


def test_high_risk_runs_when_yes_typed(tmp_path):
    marker = tmp_path / "ran"
    risky = py("a", "install", touch(marker), risk="high")
    result, _, _ = go(make_plan([risky]), tmp_path, answers=["yes"])
    assert result.outcome == "stopped" and marker.exists()


def test_manual_steps_are_listed_not_run(tmp_path):
    manual = Step(id="m", kind="manual", title="Install Docker by hand")
    result, _, err = go(
        make_plan([manual, py("a", "setup", "pass")]), tmp_path, assume_yes=True
    )
    assert result.outcome == "stopped"
    assert "not run by rtfmup: Install Docker by hand" in err
    assert state(tmp_path)["steps"]["m"]["status"] == "not_run"


def test_env_step_creates_env_without_leaking_values(tmp_path):
    (tmp_path / ".env.example").write_text("API_TOKEN=\nPORT=3000\n", encoding="utf-8")
    analysis = analyze(tmp_path)
    analysis.env = [token_var()]
    secret = "s3cr3t-value-xyz"
    err = io.StringIO()
    result = execute_plan(
        make_plan([env_step(), py("a", "setup", "pass")]),
        analysis,
        tmp_path,
        confirmer=Confirmer(read=lambda _p: "y", out=err),
        prompter=Prompter(
            interactive=True, read=lambda _p: "", secret=lambda _p: secret, out=err
        ),
        out=io.StringIO(),
        err=err,
    )
    assert result.outcome == "stopped"
    assert f"API_TOKEN={secret}" in (tmp_path / ".env").read_text(encoding="utf-8")
    dumped = (tmp_path / ".rtfmup" / "state.json").read_text(encoding="utf-8")
    assert secret not in dumped and secret not in err.getvalue()
    assert state(tmp_path)["steps"]["e"]["status"] == "ok"


def test_required_env_unfilled_stops_before_later_steps(tmp_path):
    (tmp_path / ".env.example").write_text("API_TOKEN=\n", encoding="utf-8")
    marker = tmp_path / "ran"
    analysis = analyze(tmp_path)
    analysis.env = [token_var()]
    result = execute_plan(
        make_plan([env_step(), py("a", "setup", touch(marker))]),
        analysis,
        tmp_path,
        confirmer=Confirmer(assume_yes=True, out=io.StringIO()),
        assume_yes=True,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert result.outcome == "failed" and "API_TOKEN" in result.message
    assert not marker.exists()


def test_run_step_reaches_up_then_ctrl_c(tmp_path):
    port = free_port()
    run = Step(
        id="r",
        kind="run",
        title="serve",
        argv=[sys.executable, "-c", SERVER, str(port)],
        long_running=True,
    )
    cancel = threading.Event()
    err = io.StringIO()

    def stop_when_up():
        for _ in range(600):
            if "Up:" in err.getvalue():
                break
            time.sleep(0.05)
        cancel.set()

    threading.Thread(target=stop_when_up, daemon=True).start()
    result = execute_plan(
        make_plan(
            [py("i", "install", "pass"), run], health=Port(port=port, evidence=[])
        ),
        analyze(tmp_path),
        tmp_path,
        confirmer=Confirmer(assume_yes=True, out=err),
        assume_yes=True,
        out=io.StringIO(),
        err=err,
        cancel=cancel,
        health_timeout=30,
    )
    assert result.outcome == "up" and result.exit_code == 0, (
        f"{result.outcome}: {result.message} | {err.getvalue()!r}"
    )
    assert f"Up: http://localhost:{port}" in err.getvalue()
    s = state(tmp_path)
    assert s["steps"]["r"]["status"] == "cancelled" and s["outcome"] == "up"


def test_run_step_that_never_listens_fails(tmp_path):
    run = py("r", "run", "import time; time.sleep(60)", long_running=True)
    result, _, _ = go(
        make_plan([run], health=Port(port=free_port(), evidence=[])),
        tmp_path,
        assume_yes=True,
        health_timeout=1,
    )
    assert result.outcome == "failed" and "not up after" in result.message


def test_runtime_step_refreshes_path_for_later_steps(tmp_path, monkeypatch):
    new_dir = str(tmp_path / "freshly-installed")
    monkeypatch.setattr(
        "rtfmup.execute.up.refreshed_path",
        lambda current: new_dir + os.pathsep + current,
    )
    seen = tmp_path / "path.txt"
    steps = [
        py("rt", "runtime", "pass"),
        py(
            "after",
            "setup",
            f"import os; open({str(seen)!r}, 'w').write(os.environ['PATH'])",
        ),
    ]
    result, _, _ = go(make_plan(steps), tmp_path, assume_yes=True)
    assert result.outcome == "stopped"
    assert seen.read_text().startswith(new_dir)


def test_working_directory_gap_blocks_too(tmp_path):
    gap = Gap(kind="working_directory", message="cd nowhere: not in this repo")
    marker = tmp_path / "ran"
    step = py("a", "setup", touch(marker))
    result, _, err = go(make_plan([step], gaps=[gap]), tmp_path, assume_yes=True)
    assert result.outcome == "blocked" and not marker.exists()
    assert "cd nowhere" in err


def test_a_declined_run_step_is_not_reported_as_success(tmp_path):
    """express: setup ran, the high-risk run step was skipped, and up exited 0."""
    setup = py("a", "setup", "pass")
    run = py("r", "run", "pass", long_running=True).model_copy(update={"risk": "high"})
    result, _, _ = go(make_plan([setup, run]), tmp_path, assume_yes=True)
    assert result.outcome == "declined" and result.exit_code != 0


# --- T4.2 resume -------------------------------------------------------------------


def _resume_plan(root: Path, *, second_code: str | None = None) -> Plan:
    ok = root / ".ok"
    return make_plan(
        [
            py("a", "install", touch(root / "a.done")),
            py(
                "b",
                "install",
                second_code
                or f"import os, sys; sys.exit(0 if os.path.exists({str(ok)!r}) else 1)",
            ),
            py("c", "setup", touch(root / "c.done")),
        ]
    )


def test_resume_skips_finished_steps_and_runs_the_rest(tmp_path):
    plan = _resume_plan(tmp_path)
    first, _o, _e = go(plan, tmp_path, assume_yes=True)
    assert first.exit_code == 1
    assert state(tmp_path)["plan_hash"]
    (tmp_path / "a.done").unlink()  # prove step a is not run again
    (tmp_path / ".ok").write_text("")
    second, _o, err = go(plan, tmp_path, assume_yes=True, resume=True)
    assert second.exit_code == 0, err
    assert "already done" in err
    assert not (tmp_path / "a.done").exists()
    assert (tmp_path / "c.done").exists()
    assert state(tmp_path)["steps"]["a"]["status"] == "ok"
    assert state(tmp_path)["steps"]["b"]["status"] == "ok"


def test_resume_refuses_when_the_plan_changed(tmp_path):
    first, _o, _e = go(_resume_plan(tmp_path), tmp_path, assume_yes=True)
    assert first.exit_code == 1
    changed = _resume_plan(tmp_path, second_code="raise SystemExit(1)")
    result, _o, _e = go(changed, tmp_path, assume_yes=True, resume=True)
    assert result.exit_code == 2
    assert "the plan changed since the last run" in result.message
    assert "without --resume" in result.message
    assert not (tmp_path / "c.done").exists()


def test_resume_without_a_state_file_runs_nothing(tmp_path):
    result, _o, _e = go(_resume_plan(tmp_path), tmp_path, assume_yes=True, resume=True)
    assert result.exit_code == 2
    assert "no earlier run" in result.message
    assert not (tmp_path / "a.done").exists()


def test_resume_when_everything_is_done_succeeds(tmp_path):
    (tmp_path / ".ok").write_text("")
    plan = _resume_plan(tmp_path)
    assert go(plan, tmp_path, assume_yes=True)[0].exit_code == 0
    result, _o, _e = go(plan, tmp_path, assume_yes=True, resume=True)
    assert result.exit_code == 0 and "already done" in result.message


def test_resume_refuses_after_a_readme_edit(tmp_path):
    """The hash is over the real plan: a README command change invalidates it."""
    from hosts import windows_full
    from test_plan_rules import _readme, _repo

    from rtfmup.plan.build import build_plan

    root = _repo(tmp_path, "r", {"README.md": _readme("echo one\necho two")})

    def plan() -> Plan:
        return build_plan(analyze(root), windows_full())

    go(plan(), root, assume_yes=True)
    (root / "README.md").write_text(_readme("echo one\necho three"), newline="\n")
    result, _o, _e = go(plan(), root, assume_yes=True, resume=True)
    assert result.exit_code == 2 and "plan changed" in result.message


def test_resume_survives_a_git_checkout_made_dirty_by_the_first_run(tmp_path):
    """A step's untracked output makes the checkout dirty; that alone is no change."""
    from conftest import _git
    from hosts import windows_full
    from test_plan_rules import _readme, _repo

    from rtfmup.plan.build import build_plan

    root = _repo(tmp_path, "r", {"README.md": _readme("echo one\necho two")})
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    clean = analyze(root)
    assert clean.git_dirty is False
    go(build_plan(clean, windows_full()), root, assume_yes=True)
    (root / "build-output.txt").write_text("made by a step")
    dirty = analyze(root)
    assert dirty.git_dirty is True
    result, _o, _e = go(
        build_plan(dirty, windows_full()), root, assume_yes=True, resume=True
    )
    # Refused resumes run nothing and say why; whether `echo` itself can start
    # depends on the host (no echo.exe on a bare Windows runner).
    assert result.outcome != "cannot_resume", result.message


# --- T4.3 quiet and blocking -------------------------------------------------------


def test_quiet_hides_child_output_but_shows_a_failing_steps_tail(tmp_path):
    ok = py("a", "install", "print('chatty-' + 'ok-line')")
    bad = py(
        "b",
        "install",
        "[print(f'noise {i}') for i in range(60)]; raise SystemExit(1)",
    )
    result, out, err = go(make_plan([ok, bad]), tmp_path, assume_yes=True, quiet=True)
    assert result.exit_code == 1
    assert "chatty-ok-line" not in out + err
    assert "[1/2] install" in err and "[2/2] install" in err  # stage headers stay
    assert "last 40 lines of output" in err
    assert "noise 59" in err and "noise 20" in err  # the last 40: 20..59
    assert "noise 19" not in err


def test_a_plan_of_only_other_os_steps_blocks_with_exit_4(tmp_path):
    step = py("a", "install", "pass", platform_ok=False)
    result, _o, _err = go(make_plan([step]), tmp_path, assume_yes=True)
    assert result.outcome == "blocked" and result.exit_code == 4
    assert not (tmp_path / ".rtfmup" / "state.json").exists()


def test_an_empty_plan_is_nothing_to_run_exit_1(tmp_path):
    result, _o, _e = go(make_plan([]), tmp_path, assume_yes=True)
    assert result.outcome == "nothing" and result.exit_code == 1


def test_unix_runtime_step_adds_install_dirs_for_later_steps(tmp_path, monkeypatch):
    """T5.2b: on Linux/macOS the runtime step's PATH comes from unixpath, and Go's
    directory is only asked for once a go runtime step has run."""
    seen_installed: list[frozenset] = []

    def fake(current, *, installed=frozenset()):
        seen_installed.append(installed)
        return "/fresh/bin" + os.pathsep + current

    monkeypatch.setattr("rtfmup.execute.up.unix_refreshed_path", fake)
    seen = tmp_path / "path.txt"
    steps = [
        py("runtime-go-mise", "runtime", "pass"),
        py(
            "after",
            "setup",
            f"import os; open({str(seen)!r}, 'w').write(os.environ['PATH'])",
        ),
    ]
    result, _, _ = go(make_plan(steps, os_="linux"), tmp_path, assume_yes=True)
    assert result.outcome == "stopped"
    assert seen.read_text().startswith("/fresh/bin")
    assert seen_installed == [frozenset({"go"})]
