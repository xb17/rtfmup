"""T1.10 (Pass 3): conflicts and ambiguities."""

from __future__ import annotations

import pytest
from conftest import FIXTURES

from rtfmup.analyze import build_analysis
from rtfmup.models import Command, SourceRef
from rtfmup.resolve import (
    canon_run,
    parse_floor,
    readme_manager,
    referenced_files,
    variant_ambiguity,
    versions_conflict,
)

# --- version comparison -----------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "floor", "exact"),
    [
        ("20", (20,), True),
        ("v18.19.0", (18, 19, 0), True),
        ("20.x", (20,), False),
        ("go1.22.3", (1, 22, 3), True),
        ("net8.0", (8, 0), True),
        (">=3.11", (3, 11), False),
        (">=3.11,<4", (3, 11), False),
        ("^18.2", (18, 2), False),
        ("~3.11", (3, 11), False),
        ("~=3.11", (3, 11), False),
        ("^16 || ^18", (16,), False),
        ("20+", (20,), True),
    ],
)
def test_parse_floor(spec, floor, exact):
    assert parse_floor(spec) == (floor, exact)


@pytest.mark.parametrize(
    "spec", ["*", "latest", "lts/*", "stable", "nightly", "", "system"]
)
def test_parse_floor_unverifiable(spec):
    assert parse_floor(spec) is None


@pytest.mark.parametrize(
    ("readme", "manifest", "conflict"),
    [
        ("20+", ">=20", False),
        ("20", ">=20", False),
        ("18", ">=20", True),
        ("3.8+", ">=3.11", True),
        ("3.12", ">=3.11", False),
        ("3", ">=3.11", False),
        ("20+", "18", True),  # pinned below what the README requires
        ("20+", "20.11.0", False),
        ("20", "18", True),
        ("20", "20.11.0", False),
        ("1.22+", "1.22", False),
        ("1.20+", "1.22", False),
        ("18", "^16 || ^18", False),  # above the floor of the range
        ("20+", "latest", False),  # can't verify
        ("20+", "lts/*", False),
        ("16", ">=18", True),
    ],
)
def test_versions_conflict(readme, manifest, conflict):
    assert versions_conflict(readme, manifest) is conflict


# --- file references -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "files"),
    [
        ("pip install -r requirements.txt", ["requirements.txt"]),
        ("python app.py", ["app.py"]),
        ("docker compose -f deploy/compose.yml up", ["deploy/compose.yml"]),
        ("docker compose --file=x.yml up", ["x.yml"]),
        (
            "cp .env.example .env",
            [".env.example"],
        ),  # destination is created, not required
        ("bash scripts/setup.sh", ["scripts/setup.sh"]),
        ("make -f Makefile build", ["Makefile"]),
        ("cd app && npm ci && node server.js", ["server.js"]),
    ],
)
def test_referenced_files(text, files):
    assert referenced_files(text) == files


@pytest.mark.parametrize(
    "text",
    [
        "curl -o out.json https://x.example/a.json",
        "pip install https://x.example/pkg.txt",
        "npm install foo.js",  # a package name, but it has a js extension -> accepted risk below
        "source venv/bin/activate",
        "python -m venv .venv",
        "pip install -r <your-file>.txt",
        "node $HOME/x.js",
        "cat ~/x.json",
        "python /abs/app.py",
        "ls *.txt",
        "tar -xf dist/x.tar.gz",
        "git clone git@github.com:o/r.git",
        "npm install",
        "touch notes.txt",
        "echo hi > out.txt",
    ],
)
def test_referenced_files_false_positives(text):
    got = referenced_files(text)
    if text == "npm install foo.js":
        assert got == ["foo.js"]  # documented limitation: indistinguishable from a path
    else:
        assert got == [], got


def _cmd(
    text,
    path=("S",),
    category="install",
    variant=None,
    detector="readme.fence",
    flags=(),
):
    return Command(
        id="cmd-1", text=text, shell="bash", category=category, reason="x",
        section_path=list(path), variant=variant, flags=list(flags), confidence="medium",
        evidence=[SourceRef(file="README.md", line_start=1, line_end=1, detector=detector)],
    )  # fmt: skip


# --- package manager of the README -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "pm"),
    [
        ("npm install", "npm"),
        ("pnpm i", "pnpm"),
        ("yarn", "yarn"),
        ("pip3 install x", "pip"),
        ("python -m pip install x", "pip"),
        ("uv sync", "uv"),
        ("uv pip install x", "uv"),
        ("poetry install", "poetry"),
        ("cd server && poetry install", "poetry"),
        ("cargo build", None),
        ("uv tool upgrade x", None),
        ("brew install node", None),
    ],
)
def test_readme_manager(text, pm):
    assert readme_manager(text) == pm


# --- ambiguity helpers -----------------------------------------------------------------


def test_variant_ambiguity_needs_two_paths():
    native = _cmd("python app.py", category="run", variant="native")
    docker = _cmd("docker compose up", category="run", variant="docker")
    amb = variant_ambiguity([native, docker])
    assert amb and amb.options == ["docker", "native"] and amb.default is None
    assert variant_ambiguity([native]) is None
    assert variant_ambiguity([docker]) is None
    # None counts as the native path
    assert (
        variant_ambiguity([_cmd("python app.py", category="run"), docker]) is not None
    )
    # OS variants are decided by the host, so they are not a user choice
    win = _cmd("py app.py", category="run", variant="windows")
    mac = _cmd("python3 app.py", category="run", variant="macos")
    assert variant_ambiguity([win, mac]) is None


def test_variant_ambiguity_ignores_non_readme_commands():
    docker = _cmd("docker compose up", category="run", variant="docker")
    ci = _cmd("pytest", category="run", detector="ci.github")
    assert variant_ambiguity([docker, ci]) is None


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("npm run dev", "pnpm dev"),
        ("yarn start", "npm start"),
        ("npm run dev", "bun run dev"),
    ],
)
def test_canon_run_equates_package_managers(a, b):
    assert canon_run(a) == canon_run(b)


def test_canon_run_distinguishes_scripts_and_follows_cd():
    assert canon_run("npm run dev") != canon_run("npm start")
    assert canon_run("cd client && npm run dev") == canon_run("npm run dev")
    assert canon_run("cargo   run") == "cargo run"


# --- whole analyses on fixtures ------------------------------------------------------------


def analysis(name):
    return build_analysis(FIXTURES / name)


def test_stale_readme_conflicts():
    a = analysis("stale-readme")
    msgs = [c.message for c in a.conflicts]
    assert msgs == [
        "README installs with `npm` but the repository uses pnpm (pnpm-lock.yaml)",
        "README runs `pip install -r requirements.txt` but `requirements.txt` does not exist in the repository",
    ]
    assert [c.id for c in a.conflicts] == ["conflict-1", "conflict-2"]
    for c in a.conflicts:
        assert c.evidence[0].file == "README.md" and c.evidence[0].line_start


def test_stale_readme_manager_conflict_has_both_sides():
    c = analysis("stale-readme").conflicts[0]
    assert [e.file for e in c.evidence] == ["README.md", "pnpm-lock.yaml"]


def test_docker_or_native_ambiguity_and_no_conflicts():
    a = analysis("docker-or-native")
    assert a.conflicts == []
    (amb,) = a.ambiguities
    assert amb.id == "amb-variant" and amb.options == ["docker", "native"]
    assert amb.question == "Two setup paths found: native or Docker. Which one?"
    assert [e.file for e in amb.evidence] == ["README.md", "README.md"]


def test_polyglot_component_ambiguity_only():
    a = analysis("polyglot-server-client")
    assert [x.id for x in a.ambiguities] == ["amb-component"]
    assert a.conflicts == []  # `cd server` / `cd client` make the paths resolve


@pytest.mark.parametrize(
    "name",
    ["py-uv", "py-requirements", "node-pnpm", "rust-cli", "go-cli", "dotnet-web", "rails-ish", "laravel-ish",
     "py-http", "fullstack-compose", "unix-only", "windows-readme", "console-output", "mise-pinned",
     "macos-readme", "linux-readme", "no-readme-node", "empty", "mono-pnpm", "rst-readme"],
)  # fmt: skip
def test_healthy_fixtures_have_no_conflicts_or_run_ambiguity(name):
    a = analysis(name)
    assert a.conflicts == [], [c.message for c in a.conflicts]
    assert [x.id for x in a.ambiguities if x.id != "amb-component"] == []


def test_run_ambiguity_when_readme_and_manifest_disagree(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node a.js"}}')
    (tmp_path / "README.md").write_text("# X\n\n```bash\nnpm run serve:all\n```\n")
    a = build_analysis(tmp_path)
    (amb,) = a.ambiguities
    assert amb.id == "amb-run" and amb.options == ["npm run serve:all", "npm run start"]
    assert [e.file for e in amb.evidence] == ["README.md", "package.json"]


def test_run_ambiguity_not_raised_when_equivalent(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "x"}}')
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "README.md").write_text("# X\n\n```bash\nnpm run dev\n```\n")
    assert build_analysis(tmp_path).ambiguities == []


def test_runtime_conflict_readme_vs_manifest(tmp_path):
    (tmp_path / "package.json").write_text('{\n  "engines": {"node": ">=20"}\n}\n')
    (tmp_path / "README.md").write_text("# X\n\nRequires Node.js 16.\n")
    (c,) = build_analysis(tmp_path).conflicts
    assert c.message == "README says node 16 but package.json says >=20"
    assert [e.file for e in c.evidence] == ["README.md", "package.json"]
    assert c.evidence[1].line_start == 2


def test_runtime_no_conflict_when_compatible(tmp_path):
    (tmp_path / "package.json").write_text('{"engines": {"node": ">=20"}}')
    (tmp_path / "README.md").write_text("# X\n\nRequires Node.js 22+.\n")
    assert build_analysis(tmp_path).conflicts == []


def test_unverifiable_version_is_not_a_conflict(tmp_path):
    (tmp_path / ".nvmrc").write_text("lts/*\n")
    (tmp_path / "README.md").write_text("# X\n\nRequires Node.js 16.\n")
    assert build_analysis(tmp_path).conflicts == []


def test_pip_in_readme_vs_uv_lock(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "README.md").write_text("# X\n\n```bash\npip install .\n```\n")
    (c,) = build_analysis(tmp_path).conflicts
    assert "`pip`" in c.message and "uv" in c.message


def test_guessed_npm_fallback_is_not_a_conflict(tmp_path):
    # package.json without a lockfile: the detector guessed npm, so yarn in the README is fine.
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "README.md").write_text("# X\n\n```bash\nyarn install\n```\n")
    assert build_analysis(tmp_path).conflicts == []
