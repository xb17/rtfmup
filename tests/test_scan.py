import json

import pytest

from rtfmup.cli import main
from rtfmup.scan import scan


def touch(root, *names):
    for name in names:
        (root / name).write_text("", encoding="utf-8")


def test_empty_dir_finds_nothing(tmp_path):
    result = scan(tmp_path)
    assert result.findings == []
    assert result.languages == []


def test_detects_all_minimum_manifests(tmp_path):
    names = [
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Dockerfile",
        ".env.example",
    ]
    touch(tmp_path, *names)
    result = scan(tmp_path)
    assert {f.file for f in result.findings} == set(names)
    assert result.languages == ["Python", "Node.js", "Rust", "Go"]


def test_ignores_directories_named_like_manifests(tmp_path):
    (tmp_path / "package.json").mkdir()
    assert scan(tmp_path).findings == []


def test_not_a_directory_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        scan(tmp_path / "missing")


def test_cli_json_output(tmp_path, capsys):
    touch(tmp_path, "go.mod", "Dockerfile")
    assert main(["scan", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["languages"] == ["Go"]
    assert {f["file"] for f in data["findings"]} == {"go.mod", "Dockerfile"}


def test_cli_missing_path_exits_2(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "nope")]) == 2
    assert "not a directory" in capsys.readouterr().err
