"""Pass 1 detectors. Expected line numbers are checked against the fixture text."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXTURES

from rtfmup.detect import detect_dir
from rtfmup.detect._common import find_key_line


def run(name: str):
    return detect_dir(FIXTURES / name)


def runtimes(d):
    return {
        (r.name, r.version_spec, r.evidence[0].file, r.evidence[0].line_start)
        for r in d.runtimes
    }


def pms(d):
    return {(p.name, p.lockfile) for p in d.package_managers}


def line_of(fixture: str, file: str, needle: str) -> int:
    lines = (FIXTURES / fixture / file).read_text(encoding="utf-8").splitlines()
    return next(i for i, line in enumerate(lines, 1) if needle in line)


# --- find_key_line -----------------------------------------------------------


def test_find_key_line_sequential():
    text = '{\n  "a": {"node": 1},\n  "engines": {\n    "node": ">=20"\n  }\n}\n'
    assert find_key_line(text, '"engines"', '"node"') == 4
    assert find_key_line(text, '"node"') == 2
    assert find_key_line(text, '"missing"') is None
    assert find_key_line(text, '"engines"', '"missing"') == 3


# --- python ------------------------------------------------------------------


def test_python_uv():
    d = run("py-uv")
    assert pms(d) == {("uv", "uv.lock")}
    n = line_of("py-uv", "pyproject.toml", "requires-python")
    assert ("python", ">=3.11", "pyproject.toml", n) in runtimes(d)
    assert ("python", "3.12", ".python-version", 1) in runtimes(d)
    assert d.warnings == []


def test_python_requirements_only():
    d = run("py-requirements")
    assert pms(d) == {("pip", None)}
    assert d.runtimes == []
    assert [m.kind for m in d.manifests] == ["requirements_txt"]


def test_python_poetry(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "x"\n\n[tool.poetry.dependencies]\npython = "^3.10"\n'
    )
    d = detect_dir(tmp_path)
    assert pms(d) == {("poetry", None)}
    assert runtimes(d) == {("python", "^3.10", "pyproject.toml", 5)}


def test_python_pipenv_and_poetry_lock(tmp_path):
    (tmp_path / "Pipfile").write_text("[packages]\n")
    (tmp_path / "Pipfile.lock").write_text("{}")
    (tmp_path / "poetry.lock").write_text("")
    assert pms(detect_dir(tmp_path)) == {
        ("pipenv", "Pipfile.lock"),
        ("poetry", "poetry.lock"),
    }


def test_python_pyproject_without_lock_is_pip(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert pms(detect_dir(tmp_path)) == {("pip", None)}


# --- node --------------------------------------------------------------------


def test_node_pnpm_lines():
    d = run("node-pnpm")
    assert pms(d) == {("pnpm", "pnpm-lock.yaml")}
    n = line_of("node-pnpm", "package.json", '"node"')
    assert ("node", ">=20", "package.json", n) in runtimes(d)
    assert ("node", "20", ".nvmrc", 1) in runtimes(d)
    pm = d.package_managers[0]
    lines = {e.file: e.line_start for e in pm.evidence}
    assert lines["package.json"] == line_of(
        "node-pnpm", "package.json", "packageManager"
    )


def test_node_npm_lockfile():
    assert pms(run("no-readme-node")) == {("npm", "package-lock.json")}


@pytest.mark.parametrize(
    ("lockfile", "pm"),
    [("yarn.lock", "yarn"), ("bun.lock", "bun"), ("bun.lockb", "bun")],
)
def test_node_other_lockfiles(tmp_path, lockfile, pm):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / lockfile).write_text("")
    assert pms(detect_dir(tmp_path)) == {(pm, lockfile)}


def test_node_no_lockfile_defaults_to_npm(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert pms(detect_dir(tmp_path)) == {("npm", None)}


def test_node_package_manager_field_without_lockfile(tmp_path):
    (tmp_path / "package.json").write_text('{\n  "packageManager": "yarn@4.1.0"\n}\n')
    assert pms(detect_dir(tmp_path)) == {("yarn", None)}


def test_node_version_files(tmp_path):
    (tmp_path / ".node-version").write_text("# comment\n18.19.0\n")
    assert runtimes(detect_dir(tmp_path)) == {("node", "18.19.0", ".node-version", 2)}


# --- rust --------------------------------------------------------------------


def test_rust_cli():
    d = run("rust-cli")
    assert pms(d) == {("cargo", "Cargo.lock")}
    assert (
        "rust", "1.75", "Cargo.toml", line_of("rust-cli", "Cargo.toml", "rust-version")
    ) in runtimes(d)  # fmt: skip
    assert (
        "rust", "stable", "rust-toolchain.toml", line_of("rust-cli", "rust-toolchain.toml", "channel")
    ) in runtimes(d)  # fmt: skip


def test_rust_legacy_toolchain_file(tmp_path):
    (tmp_path / "rust-toolchain").write_text("nightly-2024-01-01\n")
    assert runtimes(detect_dir(tmp_path)) == {
        ("rust", "nightly-2024-01-01", "rust-toolchain", 1)
    }


# --- go ----------------------------------------------------------------------


def test_go_cli():
    d = run("go-cli")
    assert pms(d) == {("go", None)}
    assert runtimes(d) == {
        ("go", "1.22", "go.mod", 3),
        ("go", "go1.22.3", "go.mod", 5),
    }


def test_go_sum_is_lockfile(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n\ngo 1.21 // note\n")
    (tmp_path / "go.sum").write_text("")
    d = detect_dir(tmp_path)
    assert pms(d) == {("go", "go.sum")}
    assert runtimes(d) == {("go", "1.21", "go.mod", 3)}


# --- ruby / php / dotnet -----------------------------------------------------


def test_ruby():
    d = run("rails-ish")
    assert pms(d) == {("bundler", "Gemfile.lock")}
    assert (
        "ruby",
        "3.3.0",
        "Gemfile",
        line_of("rails-ish", "Gemfile", "ruby '"),
    ) in runtimes(d)
    assert ("ruby", "3.3.0", ".ruby-version", 1) in runtimes(d)


def test_php():
    d = run("laravel-ish")
    assert pms(d) == {("composer", "composer.lock")}
    assert (
        "php",
        "^8.2",
        "composer.json",
        line_of("laravel-ish", "composer.json", '"php"'),
    ) in runtimes(d)


def test_dotnet():
    d = run("dotnet-web")
    assert pms(d) == {("dotnet", None)}
    assert (
        "dotnet",
        "8.0.100",
        "global.json",
        line_of("dotnet-web", "global.json", '"version"'),
    ) in runtimes(d)
    assert (
        "dotnet",
        "net8.0",
        "Web.csproj",
        line_of("dotnet-web", "Web.csproj", "TargetFramework"),
    ) in runtimes(d)
    assert {m.kind for m in d.manifests} == {"csproj", "global_json"}


# --- version files -----------------------------------------------------------


def test_mise_and_tool_versions():
    d = run("mise-pinned")
    r = runtimes(d)
    assert ("node", "20", "mise.toml", line_of("mise-pinned", "mise.toml", "node")) in r
    assert (
        "python",
        "3.12",
        "mise.toml",
        line_of("mise-pinned", "mise.toml", "python"),
    ) in r
    assert ("node", "20.11.0", ".tool-versions", 1) in r
    assert ("python", "3.12.1", ".tool-versions", 2) in r


def test_mise_table_and_list_values(tmp_path):
    (tmp_path / "mise.toml").write_text(
        '[tools]\nnode = ["22", "20"]\ngo = { version = "1.22" }\nterraform = "1"\n'
    )
    assert runtimes(detect_dir(tmp_path)) == {
        ("node", "22", "mise.toml", 2),
        ("go", "1.22", "mise.toml", 3),
    }


# --- general -----------------------------------------------------------------


def test_empty_fixture_finds_nothing():
    d = run("empty")
    assert (d.runtimes, d.package_managers, d.manifests, d.warnings) == ([], [], [], [])


def test_evidence_paths_use_relative_prefix():
    d = detect_dir(
        FIXTURES / "polyglot-server-client" / "client",
        rel="client/",
        component="client",
    )
    assert all(p.component == "client" for p in d.package_managers)
    assert d.package_managers[0].lockfile == "client/package-lock.json"
    assert d.manifests[0].file.startswith("client/")


def test_output_is_deterministic():
    a = run("node-pnpm")
    b = run("node-pnpm")
    assert [r.model_dump() for r in a.runtimes] == [r.model_dump() for r in b.runtimes]


# --- malformed files: warn, never crash ---------------------------------------

MALFORMED = [
    ("package.json", "{not json"),
    ("package.json", "[]"),
    ("composer.json", "{oops"),
    ("global.json", ""),
    ("pyproject.toml", "[project\nname ="),
    ("Cargo.toml", "= ="),
    ("mise.toml", "[tools\n"),
    ("rust-toolchain.toml", "[toolchain]\nchannel ="),
]


@pytest.mark.parametrize(("name", "content"), MALFORMED)
def test_malformed_file_warns(tmp_path, name, content):
    (tmp_path / name).write_text(content)
    d = detect_dir(tmp_path)
    assert len(d.warnings) == 1
    assert d.warnings[0].startswith(f"{name}:")


def test_undecodable_file_warns(tmp_path):
    (tmp_path / "package.json").write_bytes(b"\xff\xfe\x00bad")
    d = detect_dir(tmp_path)
    assert d.warnings and d.warnings[0].startswith("package.json:")


def test_directory_named_like_manifest_is_ignored(tmp_path: Path):
    (tmp_path / "package.json").mkdir()
    (tmp_path / "go.mod").mkdir()
    d = detect_dir(tmp_path)
    assert (d.manifests, d.warnings) == ([], [])


def test_bom_is_tolerated(tmp_path):
    (tmp_path / "package.json").write_bytes(b'\xef\xbb\xbf{"engines": {"node": "18"}}')
    assert runtimes(detect_dir(tmp_path)) == {("node", "18", "package.json", 1)}
