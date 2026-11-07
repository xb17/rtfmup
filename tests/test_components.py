"""T1.6: component discovery (D14)."""

from __future__ import annotations

import json

import pytest
from conftest import FIXTURES

from rtfmup.detect.components import discover, expand, parse_go_work
from rtfmup.scan import scan


def ids(name: str) -> list[str]:
    return [c.id for c in discover(FIXTURES / name).components]


def test_single_component_fixture():
    d = discover(FIXTURES / "py-uv")
    assert [c.id for c in d.components] == ["root"]
    (c,) = d.components
    assert (c.path, c.runtimes, c.package_managers, c.runnable) == (
        ".",
        ["python"],
        ["uv"],
        True,
    )
    assert d.ambiguities == []


def test_empty_fixture_has_only_root():
    d = discover(FIXTURES / "empty")
    assert [c.id for c in d.components] == ["root"]
    assert not d.components[0].runnable


def test_mono_pnpm_components_sorted():
    d = discover(FIXTURES / "mono-pnpm")
    assert [c.id for c in d.components] == ["root", "apps/web", "packages/lib"]
    by_id = {c.id: c for c in d.components}
    assert by_id["apps/web"].runnable and by_id["apps/web"].scripts == {
        "dev": "node index.js"
    }
    assert not by_id["packages/lib"].runnable and not by_id["root"].runnable
    assert d.ambiguities == []  # only one runnable component


def test_mono_pnpm_members_inherit_root_manager():
    d = discover(FIXTURES / "mono-pnpm")
    members = [
        p
        for cid, det in d.detections.items()
        if cid != "root"
        for p in det.package_managers
    ]
    assert {(p.name, p.lockfile, p.component) for p in members} == {
        ("pnpm", "pnpm-lock.yaml", "apps/web"),
        ("pnpm", "pnpm-lock.yaml", "packages/lib"),
    }


def test_polyglot_components_and_ambiguity():
    d = discover(FIXTURES / "polyglot-server-client")
    assert [c.id for c in d.components] == ["root", "client", "server"]
    by_id = {c.id: c for c in d.components}
    assert by_id["server"].runtimes == ["python"] and by_id[
        "server"
    ].package_managers == ["uv"]
    assert by_id["client"].package_managers == ["npm"]
    assert (
        by_id["client"].runnable
        and by_id["server"].runnable
        and not by_id["root"].runnable
    )
    (amb,) = d.ambiguities
    assert amb.options == ["client", "server"] and amb.default is None
    assert {e.file for e in amb.evidence} == {
        "client/package.json",
        "server/pyproject.toml",
    }


def test_evidence_paths_are_repo_relative():
    merged = discover(FIXTURES / "polyglot-server-client").merged(
        FIXTURES / "polyglot-server-client"
    )
    assert {m.file for m in merged.manifests} >= {
        "client/package.json",
        "server/pyproject.toml",
    }
    assert {p.component for p in merged.package_managers} == {"client", "server"}


def test_merged_output_is_deterministic():
    root = FIXTURES / "polyglot-server-client"
    a = discover(root).merged(root)
    b = discover(root).merged(root)
    assert [m.model_dump() for m in a.manifests] == [
        m.model_dump() for m in b.manifests
    ]


def test_command_ids_renumbered_across_components(tmp_path):
    for sub in ("server", "client"):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "Procfile").write_text("web: run\n")
        (tmp_path / sub / "package.json").write_text("{}")
    merged = discover(tmp_path).merged(tmp_path)
    assert [c.id for c in merged.commands] == ["cmd-1", "cmd-2"]
    assert [c.evidence[0].file for c in merged.commands] == [
        "client/Procfile",
        "server/Procfile",
    ]


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {"package.json": '{"workspaces": ["libs/*"]}', "libs/a/package.json": "{}"},
            ["root", "libs/a"],
        ),
        (
            {
                "package.json": '{"workspaces": {"packages": ["libs/*"]}}',
                "libs/a/package.json": "{}",
            },
            ["root", "libs/a"],
        ),
        (
            {
                "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
                "crates/x/Cargo.toml": '[package]\nname="x"\n',
            },
            ["root", "crates/x"],
        ),
        (
            {
                "pyproject.toml": '[tool.uv.workspace]\nmembers = ["pkgs/one"]\n',
                "pkgs/one/pyproject.toml": '[project]\nname="one"\n',
            },
            ["root", "pkgs/one"],
        ),
        (
            {
                "go.work": "go 1.22\n\nuse (\n\t./svc/a\n\t./svc/b\n)\n",
                "svc/a/go.mod": "module a\n",
                "svc/b/go.mod": "module b\n",
            },
            ["root", "svc/a", "svc/b"],
        ),
        (
            {"go.work": "go 1.22\nuse ./one\n", "one/go.mod": "module one\n"},
            ["root", "one"],
        ),
        (
            {
                "pnpm-workspace.yaml": "packages:\n  - 'tools/*'\n",
                "tools/t/package.json": "{}",
            },
            ["root", "tools/t"],
        ),
        # conventional dirs need no declaration
        (
            {"backend/requirements.txt": "x\n", "frontend/package.json": "{}"},
            ["root", "backend", "frontend"],
        ),
        ({"services/s1/go.mod": "module s1\n"}, ["root", "services/s1"]),
        # a conventional directory without any manifest is not a component
        ({"web/index.html": "<html>"}, ["root"]),
        ({"docs/package.json": "{}"}, ["root"]),  # `docs` is not conventional
    ],
)
def test_workspace_and_conventional_discovery(tmp_path, files, expected):
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    assert [c.id for c in discover(tmp_path).components] == expected


def test_workspace_cannot_escape_repo(tmp_path):
    repo = tmp_path / "repo"
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "package.json").write_text("{}")
    repo.mkdir()
    (repo / "package.json").write_text('{"workspaces": ["../outside", "/abs", "!x"]}')
    assert [c.id for c in discover(repo).components] == ["root"]


def test_expand_ignores_hidden_and_node_modules(tmp_path):
    for d in (".hidden/a", "node_modules/b", "real/c"):
        (tmp_path / d).mkdir(parents=True)
    assert [p.name for p in expand(tmp_path, "*/*")] == ["c"]


def test_component_cap_warns(tmp_path):
    for i in range(105):
        sub = tmp_path / "packages" / f"p{i:03}"
        sub.mkdir(parents=True)
        (sub / "package.json").write_text("{}")
    d = discover(tmp_path)
    assert len(d.components) == 100
    assert any("more than 100" in w for w in d.warnings)


def test_parse_go_work():
    assert parse_go_work('use "./a" // c\nuse (\n  ./b\n  // skip\n)\n') == [
        "./a",
        "./b",
    ]


def test_runnable_heuristics(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="x"\n\n[project.scripts]\nx = "x:main"\n'
    )
    assert discover(tmp_path).components[0].runnable
    lib = tmp_path / "lib"
    lib.mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\n')
    (lib / "pyproject.toml").write_text('[project]\nname="lib"\n')
    assert not any(c.runnable for c in discover(tmp_path).components)
    (lib / "pyproject.toml").write_text('[project]\nname="lib"\n')
    (tmp_path / "Makefile").write_text("run:\n\techo hi\n")
    assert discover(tmp_path).components[0].runnable


def test_scan_json_includes_components_and_ambiguities():
    result = scan(FIXTURES / "polyglot-server-client")
    data = json.loads(json.dumps(result.to_dict()))
    assert [c["id"] for c in data["components"]] == ["root", "client", "server"]
    assert data["ambiguities"][0]["options"] == ["client", "server"]
