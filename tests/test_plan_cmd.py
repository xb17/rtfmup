"""T2.5: the `plan` command."""

from __future__ import annotations

import json
import subprocess

import pytest
from hosts import windows_full
from typer.testing import CliRunner

from rtfmup.cli import app
from rtfmup.plan.model import Plan

runner = CliRunner()


@pytest.fixture(autouse=True)
def fake_host(monkeypatch):
    monkeypatch.setattr("rtfmup.cli.probe_host", windows_full)
    monkeypatch.setattr("rtfmup.cli._is_interactive", lambda: False)


def test_plan_json_is_valid_and_matches_the_saved_file(fixture_copy):
    root = fixture_copy("py-uv", git=True)
    result = runner.invoke(app, ["plan", str(root), "--json"])
    assert result.exit_code == 0, result.output
    plan = Plan.model_validate_json(result.stdout)
    saved = root / ".rtfmup" / "plan.json"
    assert saved.read_text(encoding="utf-8") == result.stdout
    assert plan.component == "root" and plan.steps[-1].kind == "run"
    assert b"\r\n" not in saved.read_bytes()  # LF on every OS


def test_plan_ignores_the_state_dir_in_git(fixture_copy):
    root = fixture_copy("py-uv", git=True)
    runner.invoke(app, ["plan", str(root), "--json"])
    exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert ".rtfmup/" in exclude.splitlines()
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status.strip() == ""  # nothing new for the user to commit


def test_plan_human_output_shows_steps_gaps_and_that_nothing_ran(fixture_copy):
    root = fixture_copy("fullstack-compose")
    result = runner.invoke(app, ["plan", str(root)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "npm ci" in out and "medium" in out
    assert "STRIPE_API_KEY" in out and "Gaps" in out
    assert "Nothing was executed" in out


def test_plan_does_not_execute_anything(fixture_copy, monkeypatch):
    calls: list[list[str]] = []
    real_run = subprocess.run

    def spy(args, *a, **kw):
        calls.append(list(args) if not isinstance(args, str) else [args])
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    root = fixture_copy("unix-only")
    assert runner.invoke(app, ["plan", str(root)]).exit_code == 0
    for argv in calls:
        assert argv[0] == "git", argv  # only read-only git queries
        assert "install" not in argv and "sudo" not in argv


def test_unanswered_question_without_a_terminal_exits_2_naming_the_flag(fixture_copy):
    root = fixture_copy("docker-or-native")
    result = runner.invoke(app, ["plan", str(root)])
    assert result.exit_code == 2
    assert "--variant" in result.output and "docker" in result.output
    assert not (root / ".rtfmup" / "plan.json").exists()


def test_flags_answer_the_questions(fixture_copy):
    root = fixture_copy("docker-or-native")
    result = runner.invoke(app, ["plan", str(root), "--variant", "docker", "--json"])
    assert result.exit_code == 0, result.output
    assert Plan.model_validate_json(result.stdout).variant == "docker"


def test_component_flag(fixture_copy):
    root = fixture_copy("polyglot-server-client")
    result = runner.invoke(app, ["plan", str(root), "--component", "server", "--json"])
    plan = Plan.model_validate_json(result.stdout)
    assert plan.component == "server"
    assert all(s.cwd == "server" for s in plan.steps if s.kind in ("install", "run"))


def test_bad_choice_exits_2(fixture_copy):
    root = fixture_copy("docker-or-native")
    result = runner.invoke(app, ["plan", str(root), "--variant", "podman"])
    assert result.exit_code == 2 and "podman" in result.output


def test_interactive_prompt_answers_by_number(fixture_copy, monkeypatch):
    monkeypatch.setattr("rtfmup.cli._is_interactive", lambda: True)
    root = fixture_copy("docker-or-native")
    # options are sorted: 1) docker 2) native; "9" is rejected and asked again
    result = runner.invoke(app, ["plan", str(root), "--json"], input="9\n2\n")
    assert result.exit_code == 0, result.output
    body = result.stdout[result.stdout.index("{") :]  # the runner echoes typed input
    assert Plan.model_validate_json(body).variant == "native"
    assert "Which one?" in result.output and "Enter 1-2" in result.output


def test_missing_directory_exits_2(tmp_path):
    result = runner.invoke(app, ["plan", str(tmp_path / "nope")])
    assert result.exit_code == 2 and "not a directory" in result.output


def test_plan_of_a_file_url_skips_the_clone_of_itself(tmp_path, monkeypatch):
    remote = tmp_path / "widget"
    remote.mkdir()
    (remote / "package.json").write_text('{"name":"w","scripts":{"start":"node s.js"}}')
    url = remote.as_uri()
    (remote / "README.md").write_text(
        f"# w\n\n## Install\n\n```bash\ngit clone {url}\ncd widget\nnpm install\nnpm start\n```\n"
    )
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            cwd=remote, check=True, capture_output=True,
        )  # fmt: skip
    for var in ("LOCALAPPDATA", "XDG_CACHE_HOME", "HOME"):
        monkeypatch.setenv(var, str(tmp_path / "cache"))
    result = runner.invoke(app, ["plan", url, "--json"])
    assert result.exit_code == 0, result.output
    texts = json.dumps([s["argv"] for s in json.loads(result.stdout)["steps"]])
    assert "clone" not in texts and "npm" in texts
