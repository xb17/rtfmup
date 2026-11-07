import subprocess

import pytest

from conftest import FIXTURES

EXPECTED = {
    "empty", "no-readme-node", "py-uv", "py-requirements", "node-pnpm",
    "mono-pnpm", "polyglot-server-client", "docker-or-native",
    "fullstack-compose", "stale-readme", "unix-only", "windows-readme",
    "console-output", "rust-cli", "go-cli", "dotnet-web", "rails-ish",
    "laravel-ish", "py-http", "rst-readme", "deploy-mixed", "k8s-manifests",
    "macos-readme", "linux-readme", "mise-pinned",
}  # fmt: skip


def test_all_planned_fixtures_exist():
    assert {p.name for p in FIXTURES.iterdir() if p.is_dir()} == EXPECTED


def test_fixtures_are_not_git_repos():
    assert not list(FIXTURES.rglob(".git"))


def test_fixture_copy_without_git(fixture_copy):
    dest = fixture_copy("py-uv")
    assert (dest / "pyproject.toml").is_file()
    assert not (dest / ".git").exists()


def test_fixture_copy_with_git(fixture_copy):
    dest = fixture_copy("py-uv", git=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True
    )
    assert head.returncode == 0 and len(head.stdout.strip()) == 40


def test_fixture_copy_unknown_name(fixture_copy):
    with pytest.raises(FileNotFoundError):
        fixture_copy("nope")
