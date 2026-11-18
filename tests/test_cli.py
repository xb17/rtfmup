import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from rtfmup import __version__
from rtfmup.cli import app, main, shim

FIXTURE = Path(__file__).parent / "fixtures" / "py-uv"
runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"rtfmup {__version__}"


def test_scan_json():
    result = runner.invoke(app, ["scan", str(FIXTURE), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["schema_version"] == "1"
    assert [m["name"] for m in data["package_managers"]] == ["uv"]


def test_scan_missing_path_exits_2(tmp_path):
    result = runner.invoke(app, ["scan", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "not a directory" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["config"],
        ["recipe", "show", "x"],
    ],
)
def test_stub_commands_exit_2(args):
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert "not implemented yet" in result.output


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["scan", "."], ["scan", "."]),
        (["--version"], ["--version"]),
        (["--help"], ["--help"]),
        (["owner/repo"], ["up", "owner/repo"]),
        (["https://example.com/r.git"], ["up", "https://example.com/r.git"]),
        (["up", "scan"], ["up", "scan"]),
        ([], []),
    ],
)
def test_shim(argv, expected):
    assert shim(argv) == expected


def test_bare_target_means_up(capsys):
    # A missing directory is a usage error from `up`, not "unknown command".
    assert main([str(FIXTURE / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_scan_is_not_up_scan(capsys):
    # `rtfmup scan` runs scan; `rtfmup up scan` treats "scan" as a target.
    assert main(["scan", str(FIXTURE)]) == 0
    capsys.readouterr()
    assert main(["up", "scan"]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_scan_json_validates_against_the_schema_and_is_lf():
    from rtfmup.models import Analysis

    result = runner.invoke(app, ["scan", str(FIXTURE), "--json"])
    assert Analysis.model_validate_json(result.output).readme == "README.md"
    assert chr(13) not in result.output


def test_scan_summary_mentions_the_essentials():
    result = runner.invoke(app, ["scan", str(FIXTURE)])
    assert result.exit_code == 0
    for text in ("Components", "uv", "README.md", "Commands:"):
        assert text in result.output


def test_scan_fresh_flag_is_accepted():
    assert (
        runner.invoke(app, ["scan", str(FIXTURE), "--fresh", "--json"]).exit_code == 0
    )


def test_scan_default_target_is_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["scan", "--json"])
    assert result.exit_code == 0 and json.loads(result.output)["readme"] is None


def test_scan_git_url_failure_exits_2(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    result = runner.invoke(app, ["scan", (tmp_path / "missing-repo").as_uri()])
    assert result.exit_code == 2
    assert "git clone" in result.output


def test_scan_a_file_url_end_to_end(tmp_path, monkeypatch):
    import subprocess

    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "go.mod").write_text("module x\n\ngo 1.22\n")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
            cwd=remote, check=True, capture_output=True,
        )  # fmt: skip
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("HOME", str(tmp_path / "cache"))
    result = runner.invoke(app, ["scan", remote.as_uri(), "--json"])
    assert result.exit_code == 0, result.output
    start = result.output.index("{")
    data = json.loads(result.output[start:])
    assert [p["name"] for p in data["package_managers"]] == ["go"]
    assert data["git_head"] and data["git_dirty"] is False


def _fake_profile():
    from rtfmup.host.probe import HostProfile, ToolInfo

    return HostProfile(
        os="windows",
        arch="x86_64",
        tools={
            "git": ToolInfo(found=True, path="C:/g/git.exe", version="2.56.0"),
            "python": ToolInfo(
                found=True, version="3.12.11", versions=["3.12.11", "3.11.14"]
            ),
            "scoop": ToolInfo(found=False),
        },
        docker_daemon=None,
    )


def test_doctor_human_output(monkeypatch):
    monkeypatch.setattr("rtfmup.cli.probe_host", _fake_profile)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "windows" in result.output and "x86_64" in result.output
    assert "2.56.0" in result.output
    assert "3.12.11, 3.11.14" in result.output
    assert "not found" in result.output  # scoop


def test_doctor_json(monkeypatch):
    monkeypatch.setattr("rtfmup.cli.probe_host", _fake_profile)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["os"] == "windows"
    assert data["tools"]["git"]["version"] == "2.56.0"


def test_up_on_linux_host_stops_before_running_anything(tmp_path, monkeypatch, capsys):
    import shutil

    from hosts import linux_full

    repo = tmp_path / "py-http"
    shutil.copytree(
        FIXTURE.parent / "py-http", repo, ignore=shutil.ignore_patterns(".rtfmup")
    )
    monkeypatch.setattr("rtfmup.cli.probe_host", linux_full)
    assert main(["up", str(repo), "--yes"]) == 2
    err = capsys.readouterr().err
    assert "not yet supported on this OS" in err
    assert not (repo / ".env").exists() and not (repo / ".venv").exists()


# --- T4.3 exit codes ----------------------------------------------------------------

from rtfmup import exitcodes
from rtfmup.execute.up import UpResult

README = Path(__file__).parents[1] / "README.md"


@pytest.fixture
def repo(fixture_copy):
    """A throwaway copy: plan and up write .rtfmup/ into the target."""
    return fixture_copy("py-uv")


def _up(monkeypatch, repo, outcome: str, *extra: str):
    monkeypatch.setattr(
        "rtfmup.cli.execute_plan", lambda *a, **k: UpResult(outcome, "msg")
    )
    return runner.invoke(app, ["up", str(repo), *extra])


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        ("up", exitcodes.EXIT_OK),
        ("stopped", exitcodes.EXIT_OK),
        ("failed", exitcodes.EXIT_STEP_FAILED),
        ("nothing", exitcodes.EXIT_STEP_FAILED),
        ("unsupported", exitcodes.EXIT_USAGE),
        ("cannot_resume", exitcodes.EXIT_USAGE),
        ("declined", exitcodes.EXIT_DECLINED),
        ("blocked", exitcodes.EXIT_GAPS_BLOCK),
    ],
)
def test_exit_code_per_up_outcome(monkeypatch, repo, outcome, code):
    assert _up(monkeypatch, repo, outcome).exit_code == code


def test_exit_usage_for_bad_target(tmp_path):
    assert runner.invoke(app, ["up", str(tmp_path / "nope")]).exit_code == 2


def test_exit_usage_for_quiet_with_verbose(monkeypatch, repo):
    result = _up(monkeypatch, repo, "up", "--quiet", "--verbose")
    assert result.exit_code == 2 and "cannot be used together" in result.output


def test_exit_codes_in_readme_match_the_module():
    text = README.read_text(encoding="utf-8")
    rows = {
        int(m.group(1)): (m.group(2).strip(), m.group(3).strip())
        for m in re.finditer(r"^\| (\d) \| ([^|]+) \| ([^|]+) \|$", text, re.MULTILINE)
    }
    assert rows == {c: (n, d) for c, n, d in exitcodes.TABLE}


def test_exit_codes_are_defined_only_in_the_exitcodes_module():
    src = (Path(__file__).parents[1] / "src" / "rtfmup").rglob("*.py")
    offenders = [
        p.name
        for p in src
        if p.name != "exitcodes.py"
        and re.search(r"^EXIT_\w+\s*=", p.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    assert offenders == []


def test_verbose_plan_adds_host_and_analysis_before_the_plan(repo):
    result = runner.invoke(app, ["plan", str(repo), "--verbose"])
    assert result.exit_code == 0
    out = result.output
    assert "rtfmup doctor" in out
    assert out.index("rtfmup doctor") < out.index("rtfmup plan")


def test_quiet_up_skips_the_plan_table(monkeypatch, repo):
    quiet = _up(monkeypatch, repo, "up", "--quiet")
    normal = _up(monkeypatch, repo, "up")
    assert "Steps (nothing has been run)" in normal.output
    assert "Steps (nothing has been run)" not in quiet.output
    assert "Plan" in quiet.output  # the stage headers stay
