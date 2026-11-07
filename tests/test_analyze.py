"""T1.10: orchestrator, determinism and the D10 cache."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from conftest import FIXTURES

from rtfmup import __version__
from rtfmup.analyze import (
    analyze,
    build_analysis,
    cache_path,
    git_state,
    read_cache,
)
from rtfmup.models import Analysis

NAMES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip


# --- determinism -------------------------------------------------------------------


@pytest.mark.parametrize("name", NAMES)
def test_two_runs_are_byte_identical(name):
    a = build_analysis(FIXTURES / name).model_dump_json()
    b = build_analysis(FIXTURES / name).model_dump_json()
    assert a == b


def test_analysis_validates_against_the_schema_model():
    for name in NAMES:
        a = build_analysis(FIXTURES / name)
        assert Analysis.model_validate_json(a.model_dump_json()) == a


def test_lists_are_sorted_by_evidence():
    a = build_analysis(FIXTURES / "fullstack-compose")
    keys = [(e.evidence[0].file, e.evidence[0].line_start or 0, e.name) for e in a.env]
    assert keys == sorted(keys)
    ids = [c.id for c in a.commands]
    assert ids == [f"cmd-{i}" for i in range(1, len(ids) + 1)]


# --- merging -----------------------------------------------------------------------


def test_node_pnpm_end_to_end():
    a = build_analysis(FIXTURES / "node-pnpm")
    assert (a.readme, a.readme_format) == ("README.md", "markdown")
    assert [(p.name, p.lockfile) for p in a.package_managers] == [
        ("pnpm", "pnpm-lock.yaml")
    ]
    # manifest runtimes and the README's runtime both appear, each with its own evidence
    assert sorted((r.evidence[0].file, r.version_spec) for r in a.runtimes) == [
        (".nvmrc", "20"), ("README.md", "20+"), ("package.json", ">=20"),
    ]  # fmt: skip
    env = {e.name: e for e in a.env}
    assert env["API_KEY"].required == "required" and env["API_KEY"].secret_like
    assert env["PORT"].default == "3000"
    assert [p.port for p in a.ports] == [3000]
    assert [c.text for c in a.commands] == [
        "pnpm install",
        "cp .env.example .env",
        "pnpm dev",
    ]


def test_env_from_readme_only_is_unknown(tmp_path):
    (tmp_path / "README.md").write_text(
        "# X\n\nSet `SESSION_SECRET` before starting.\n"
    )
    (e,) = build_analysis(tmp_path).env
    assert (e.name, e.required, e.secret_like, e.default) == (
        "SESSION_SECRET",
        "unknown",
        True,
        None,
    )


def test_ports_are_merged_across_sources():
    a = build_analysis(FIXTURES / "fullstack-compose")
    (p,) = [p for p in a.ports if p.port == 3000]
    assert {e.detector for e in p.evidence} == {"containers.compose", "readme.port"}


def test_ci_and_procfile_commands_join_readme_commands(tmp_path):
    (tmp_path / "Procfile").write_text("web: gunicorn app:app\n")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(
        "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make test\n"
    )
    (tmp_path / "README.md").write_text("# X\n\n```bash\nmake\n```\n")
    a = build_analysis(tmp_path)
    assert {(c.evidence[0].detector, c.text) for c in a.commands} == {
        ("deploy.procfile", "gunicorn app:app"),
        ("ci.github", "make test"),
        ("readme.fence", "make"),
    }
    assert all(
        c.confidence == "low"
        for c in a.commands
        if c.evidence[0].detector != "readme.fence"
    )


def test_rst_readme_flows_through():
    a = build_analysis(FIXTURES / "rst-readme")
    assert (a.readme, a.readme_format) == ("README.rst", "rst")
    assert [c.text for c in a.commands][:2] == ["uv sync", "uv run py-uv-fixture"]


def test_no_readme_is_fine():
    a = build_analysis(FIXTURES / "no-readme-node")
    assert a.readme is None and a.readme_format is None and a.commands == []


def test_unsupported_readme_warns(tmp_path):
    (tmp_path / "README.txt").write_text("hi")
    assert build_analysis(tmp_path).warnings == [
        "README.txt: unsupported README format"
    ]


def test_unreadable_readme_warns(tmp_path):
    (tmp_path / "README.md").write_bytes(b"\xff\xfe\x00bad")
    a = build_analysis(tmp_path)
    assert (
        a.readme is None
        and a.warnings
        and a.warnings[0].startswith("README.md: cannot read")
    )


def test_malformed_manifest_warning_is_reported(tmp_path):
    (tmp_path / "package.json").write_text("{nope")
    a = build_analysis(tmp_path)
    assert any(w.startswith("package.json: invalid JSON") for w in a.warnings)


def test_empty_directory():
    a = build_analysis(FIXTURES / "empty")
    assert a.components[0].id == "root" and a.commands == [] and a.conflicts == []
    assert (a.git_head, a.git_dirty) == (
        None,
        None,
    ) or a.git_head is not None  # repo may contain it


def test_not_a_directory_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        analyze(tmp_path / "missing")


# --- git state ---------------------------------------------------------------------


def test_git_state_clean_dirty_and_non_repo(fixture_copy, tmp_path):
    repo = fixture_copy("py-uv", git=True)
    head, dirty = git_state(repo)
    assert head == _git(repo, "rev-parse", "HEAD").strip() and dirty is False
    (repo / "new.txt").write_text("x")
    assert git_state(repo)[1] is True
    plain = tmp_path / "plain"
    plain.mkdir()
    assert git_state(plain) == (None, None)


def test_git_state_subdirectory_of_a_repo_has_no_identity(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    (repo / "sub").mkdir()
    assert git_state(repo / "sub") == (None, None)


def test_git_state_ignores_repo_fsmonitor(fixture_copy, tmp_path):
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\necho x > '{marker.as_posix()}'\n")
    repo = fixture_copy("py-uv", git=True)
    _git(repo, "config", "core.fsmonitor", hook.as_posix())
    git_state(repo)
    assert not marker.exists()  # repo config must never run programs during detection


# --- cache (D10) -------------------------------------------------------------------


def test_cache_written_for_clean_repo_and_excluded(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    first = analyze(repo)
    assert cache_path(repo).is_file()
    assert (
        Analysis.model_validate_json(cache_path(repo).read_text(encoding="utf-8"))
        == first
    )
    assert (
        ".rtfmup/"
        in (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    )
    assert (
        _git(repo, "status", "--porcelain").strip() == ""
    )  # still clean: not tracked, not shown
    assert not (repo / ".gitignore").exists()


def test_cache_hit_returns_cached_file_content(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    analyze(repo)
    data = json.loads(cache_path(repo).read_text(encoding="utf-8"))
    data["warnings"] = ["from cache"]
    cache_path(repo).write_text(json.dumps(data), encoding="utf-8")
    assert analyze(repo).warnings == ["from cache"]  # served from the cache
    assert (
        analyze(repo, fresh=True).warnings == []
    )  # --fresh bypasses it (and rewrites)
    assert analyze(repo).warnings == []


def test_cache_invalidated_by_new_commit(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    old = analyze(repo)
    (repo / "README.md").write_text("# changed\n")
    _git(repo, "commit", "-qam", "change")
    new = analyze(repo)
    assert new.git_head != old.git_head and new.readme is not None
    assert read_cache(repo, new.git_head, False) == new


def test_dirty_tree_is_never_cached_or_served(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    analyze(repo)
    cache = cache_path(repo).read_text(encoding="utf-8")
    (repo / "extra.txt").write_text("x")
    dirty = analyze(repo)
    assert dirty.git_dirty is True
    assert cache_path(repo).read_text(encoding="utf-8") == cache  # untouched
    # a dirty tree is re-analyzed each time, so new content is seen
    (repo / "README.md").write_text("# x\n\n```bash\nmake\n```\n")
    assert [c.text for c in analyze(repo).commands] == ["make"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rtfmup_version", "9.9.9"),
        ("git_head", "0" * 40),
        ("root", "/elsewhere"),
        ("git_dirty", True),
    ],
)
def test_cache_key_mismatch_forces_reanalysis(fixture_copy, field, value):
    repo = fixture_copy("py-uv", git=True)
    analyze(repo)
    data = json.loads(cache_path(repo).read_text(encoding="utf-8"))
    data[field] = value
    data["warnings"] = ["stale"]
    cache_path(repo).write_text(json.dumps(data), encoding="utf-8")
    assert analyze(repo).warnings == []


def test_corrupt_cache_is_ignored(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    analyze(repo)
    cache_path(repo).write_text("{not json", encoding="utf-8")
    assert analyze(repo).rtfmup_version == __version__


def test_cache_with_extra_keys_is_rejected(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    analyze(repo)
    data = json.loads(cache_path(repo).read_text(encoding="utf-8"))
    data["surprise"] = 1
    data["warnings"] = ["stale"]
    cache_path(repo).write_text(json.dumps(data), encoding="utf-8")
    assert analyze(repo).warnings == []  # extra="forbid" -> invalid -> reanalyze


def test_non_git_directory_has_no_cache(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    analyze(tmp_path)
    assert not (tmp_path / ".rtfmup").exists()


def test_cached_and_fresh_are_byte_identical(fixture_copy):
    repo = fixture_copy("node-pnpm", git=True)
    fresh = analyze(repo, fresh=True).model_dump_json()
    assert analyze(repo).model_dump_json() == fresh
    assert analyze(repo, fresh=True).model_dump_json() == fresh


def test_exclude_entry_is_not_duplicated(fixture_copy):
    repo = fixture_copy("py-uv", git=True)
    for _ in range(3):
        analyze(repo, fresh=True)
    lines = (
        (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    )
    assert lines.count(".rtfmup/") == 1
