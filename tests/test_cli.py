import json
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
    assert data["languages"] == ["Python"]


def test_scan_missing_path_exits_2(tmp_path):
    result = runner.invoke(app, ["scan", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "not a directory" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["up", "x"],
        ["plan", "x"],
        ["doctor"],
        ["config"],
        ["export", "devcontainer", "x"],
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
    assert main([str(FIXTURE)]) == 2
    assert "'up' is not implemented yet" in capsys.readouterr().err


def test_scan_is_not_up_scan(capsys):
    # `rtfmup scan` runs scan; `rtfmup up scan` treats "scan" as a target.
    assert main(["scan", str(FIXTURE)]) == 0
    capsys.readouterr()
    assert main(["up", "scan"]) == 2
    assert "'up' is not implemented yet" in capsys.readouterr().err
