"""Plan builder: targeted rule tests (goldens live in test_plan.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from hosts import linux_full, windows_full
from test_plan import FIXTURES, GOLDEN_CASES

from rtfmup.analyze import analyze
from rtfmup.plan.build import (
    BadChoice,
    Choices,
    Unanswered,
    build_plan,
    parse_clone,
    repo_key,
)

PKG = '{"name": "w", "scripts": {"start": "node s.js", "build": "echo b"}}'
PKG_DEV = '{"name": "w", "scripts": {"dev": "node s.js", "build": "echo b"}}'


def _repo(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, content in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8", newline="\n")
    return root


def _build(root: Path, choices=None, host=None, own_urls=()):
    return build_plan(
        analyze(root), host or windows_full(), choices or Choices(), own_urls=own_urls
    )


def _texts(plan) -> list[str]:
    return [
        " ".join(s.argv) if s.argv else (s.shell_text or s.title) for s in plan.steps
    ]


def _plan(tmp_path: Path, name: str, choices: Choices, host=None):
    """Copy a fixture into a fresh directory and build its plan."""
    dest = tmp_path / f"copy-{len(list(tmp_path.iterdir()))}" / name
    shutil.copytree(FIXTURES / name, dest)
    return _build(dest, choices, host)


def _readme(body: str) -> str:
    return f"# w\n\n## Setup\n\n```bash\n{body}\n```\n"


def test_kinds_are_ordered_install_env_setup_build_run_healthcheck(tmp_path):
    order = ["runtime", "install", "env", "setup", "build", "run", "healthcheck"]
    for case, choices in GOLDEN_CASES.items():
        plan = _plan(tmp_path, case.split("@")[0], choices)
        idx = [order.index(s.kind) for s in plan.steps]
        assert idx == sorted(idx), case


def test_test_and_lint_commands_are_never_planned(tmp_path):
    root = _repo(
        tmp_path,
        "t",
        {
            "package.json": PKG,
            "README.md": _readme("npm install\nnpm test\nnpx eslint .\nnpm start"),
        },
    )
    texts = _texts(_build(root))
    assert "npm test" not in texts and "npx eslint ." not in texts
    assert texts[-1] == "npm start"


def test_clone_of_the_repo_we_are_in_is_skipped_with_its_cd(tmp_path):
    body = "git clone https://github.com/acme/widget.git\ncd widget\nnpm install\nnpm start"
    root = _repo(tmp_path, "widget", {"package.json": PKG, "README.md": _readme(body)})
    plan = _build(root, own_urls=["git@github.com:acme/widget.git"])
    assert all("clone" not in t for t in _texts(plan))
    assert all(s.cwd == "." for s in plan.steps)
    assert not [g for g in plan.gaps if g.kind == "unresolved"]


def test_clone_of_a_different_repo_is_kept(tmp_path):
    body = "git clone https://github.com/acme/other.git\nnpm start"
    root = _repo(tmp_path, "widget", {"package.json": PKG, "README.md": _readme(body)})
    plan = _build(root, own_urls=["https://github.com/acme/widget"])
    assert "git clone https://github.com/acme/other.git" in _texts(plan)


def test_clone_without_known_remotes_falls_back_to_directory_name(tmp_path):
    body = "git clone https://example.com/x/widget\ncd widget\nnpm start"
    root = _repo(tmp_path, "widget", {"package.json": PKG, "README.md": _readme(body)})
    assert all("clone" not in t for t in _texts(_build(root)))


def test_cd_outside_the_repo_is_a_gap_and_does_not_move_cwd(tmp_path):
    root = _repo(
        tmp_path,
        "w",
        {"package.json": PKG, "README.md": _readme("cd /tmp/foo\nnpm start")},
    )
    plan = _build(root)
    assert {s.cwd for s in plan.steps} == {"."}
    assert any("/tmp/foo" in g.message for g in plan.gaps)


def test_readme_command_overrides_the_manifest_default(tmp_path):
    base = {"package.json": PKG, "package-lock.json": "{}"}
    default = _build(_repo(tmp_path, "a", base))
    assert _texts(default)[0] == "npm ci"  # PLAN §2.5
    readme = _readme("npm install --legacy-peer-deps")
    over = _build(_repo(tmp_path, "b", {**base, "README.md": readme}))
    assert _texts(over)[0] == "npm install --legacy-peer-deps"
    assert "npm ci" not in _texts(over)


def test_readme_for_a_different_family_does_not_override(tmp_path):
    root = _repo(
        tmp_path,
        "w",
        {
            "package.json": PKG,
            "package-lock.json": "{}",
            "README.md": _readme("sudo apt install libfoo"),
        },
    )
    texts = _texts(_build(root))
    assert "npm ci" in texts and "sudo apt install libfoo" in texts


def test_manifests_win_over_a_readme_for_another_package_manager(tmp_path):
    plan = _plan(tmp_path, "stale-readme", Choices())
    texts = _texts(plan)
    assert "pnpm install --frozen-lockfile" in texts
    assert "npm install" not in texts  # lockfile says pnpm
    assert "pip install -r requirements.txt" not in texts  # file does not exist
    assert len([g for g in plan.gaps if g.kind == "unresolved"]) == 2


def test_windows_translates_the_d17_allowlist(tmp_path):
    root = _repo(
        tmp_path,
        "w",
        {
            "package.json": PKG,
            "README.md": _readme("mkdir -p data\nexport PORT=4000\nnpm start"),
        },
    )
    plan = _build(root)
    mk, run = plan.steps[-2], plan.steps[-1]
    assert mk.shell_text and mk.shell_text.startswith("New-Item -ItemType Directory")
    assert mk.platform_ok
    assert run.env == {"PORT": "4000"}  # `export` became step env, not a step
    assert not any("export" in t for t in _texts(plan))


def test_secret_exports_are_never_stored(tmp_path):
    root = _repo(
        tmp_path,
        "w",
        {
            "package.json": PKG,
            "README.md": _readme("export API_TOKEN=abc123\nnpm start"),
        },
    )
    plan = _build(root)
    assert "abc123" not in plan.model_dump_json()
    assert any(g.kind == "missing_env" and "API_TOKEN" in g.message for g in plan.gaps)


def test_env_template_becomes_one_env_step_and_absorbs_the_cp(tmp_path):
    root = _repo(
        tmp_path,
        "w",
        {
            "package.json": PKG,
            "README.md": _readme("cp .env.example .env\nnpm start"),
            ".env.example": "DB_URL=\n",
        },
    )
    plan = _build(root)
    assert [s.kind for s in plan.steps].count("env") == 1
    assert "cp .env.example .env" not in _texts(plan)


def test_powershell_commands_are_not_posix_split(tmp_path):
    plan = _plan(tmp_path, "windows-readme", Choices())
    ps = [s for s in plan.steps if s.shell_text and "Activate.ps1" in s.shell_text]
    assert ps and ps[0].shell_text == r".\.venv\Scripts\Activate.ps1"
    assert ps[0].argv is None


def test_unix_steps_are_unsupported_on_windows_but_fine_on_linux(tmp_path):
    win = _plan(tmp_path, "unix-only", Choices())
    apt = [s for s in win.steps if "apt" in (s.argv or [])]
    assert apt and not apt[0].platform_ok
    assert any(g.kind == "unsupported_platform" for g in win.gaps)
    nix = _plan(tmp_path, "unix-only", Choices(), host=linux_full())
    assert all(s.platform_ok for s in nix.steps)
    assert not [g for g in nix.gaps if g.kind == "unsupported_platform"]


def test_build_runs_only_when_the_run_step_needs_it(tmp_path):
    def plan_for(tag: str, pkg: str, run: str, choices=None):
        body = f"npm run build\n```\n\n## Run\n\n```bash\n{run}"
        root = _repo(tmp_path, tag, {"package.json": pkg, "README.md": _readme(body)})
        return _texts(_build(root, choices))

    prod = plan_for("prod", PKG, "npm start")  # production-style script
    assert "npm run build" in prod
    assert "npm run build" not in plan_for("dev", PKG_DEV, "npm run dev")


def test_ambiguities_must_be_answered(tmp_path):
    with pytest.raises(Unanswered) as exc:
        _plan(tmp_path, "docker-or-native", Choices())
    assert [q.id for q in exc.value.questions] == ["amb-variant"]
    with pytest.raises(Unanswered):
        _plan(tmp_path, "polyglot-server-client", Choices())


def test_bad_choices_are_rejected(tmp_path):
    with pytest.raises(BadChoice):
        _plan(tmp_path, "docker-or-native", Choices(variant="podman"))
    with pytest.raises(BadChoice):
        _plan(tmp_path, "polyglot-server-client", Choices(component="nope"))


def test_plan_is_deterministic(tmp_path):
    root = tmp_path / "fullstack-compose"
    shutil.copytree(FIXTURES / "fullstack-compose", root)
    a = _build(root).model_dump_json()
    b = _build(root).model_dump_json()
    assert a == b


def test_health_port_skips_service_ports(tmp_path):
    plan = _plan(tmp_path, "docker-or-native", Choices(variant="native"))
    assert plan.health is not None and plan.health.port == 8000  # not postgres' 5432


def test_docker_variant_runs_compose(tmp_path):
    plan = _plan(tmp_path, "docker-or-native", Choices(variant="docker"))
    assert _texts(plan)[0] == "docker compose up"
    assert plan.steps[0].long_running and plan.variant == "docker"


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("git@github.com:Acme/Widget.git", "github.com/acme/widget"),
        ("https://github.com/acme/widget.git", "github.com/acme/widget"),
        ("https://github.com/acme/widget/", "github.com/acme/widget"),
        ("ssh://git@github.com/acme/widget", "github.com/acme/widget"),
        ("https://user@gitea.example/acme/widget.git", "gitea.example/acme/widget"),
    ],
)
def test_repo_key(url, key):
    assert repo_key(url) == key


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("git clone https://github.com/a/b.git", ("https://github.com/a/b.git", "b")),
        (
            "git clone https://github.com/a/b.git mydir",
            ("https://github.com/a/b.git", "mydir"),
        ),
        (
            "git clone --depth 1 -b main https://github.com/a/b",
            ("https://github.com/a/b", "b"),
        ),
        ("git clone", None),
        ("git pull", None),
    ],
)
def test_parse_clone(text, expected):
    assert parse_clone(text) == expected


def test_steps_carry_risk_from_the_readme_text_not_the_translation(tmp_path):
    root = _repo(
        tmp_path,
        "w",
        {
            "package.json": PKG,
            "README.md": _readme("cp .env.example ~/.app.env\nnpm start"),
        },
    )
    plan = _build(root)
    cp = next(s for s in plan.steps if s.shell_text and "Copy-Item" in s.shell_text)
    assert cp.risk == "high" and "outside the repo" in " ".join(cp.risk_reasons)


def test_unix_only_fixture_risk(tmp_path):
    plan = _plan(tmp_path, "unix-only", Choices())
    by_text = {" ".join(s.argv or [s.shell_text or ""]): s for s in plan.steps}
    assert by_text["sudo apt install python3-venv libpq-dev"].risk == "high"
    pipe = next(s for s in plan.steps if "install.sh" in (s.shell_text or ""))
    assert pipe.risk == "high" and any("pipes" in r for r in pipe.risk_reasons)
    assert by_text["python app.py"].risk == "low"


def test_manifest_defaults_are_medium_installs(tmp_path):
    plan = _plan(tmp_path, "node-pnpm", Choices())
    assert [s.risk for s in plan.steps if s.kind == "install"] == ["medium"]
    assert [s.risk for s in plan.steps if s.kind in ("env", "healthcheck")] == [
        "low",
        "low",
    ]


def test_a_cd_in_a_section_for_another_variant_is_not_a_gap(tmp_path):
    readme = (
        "# w\n\n## Install\n\n### Docker Compose\n\n```bash\nmkdir w\ncd w\n"
        "docker compose up -d\n```\n\n### Non-Docker\n\n```bash\nnpm start\n```\n"
    )
    root = _repo(tmp_path, "w", {"package.json": PKG, "README.md": readme})
    plan = _build(root, Choices(variant="native"))
    assert not [g for g in plan.gaps if g.kind == "unresolved"]
    assert _texts(plan)[-1] == "npm start"


def test_powershell_env_assignment_is_step_env_like_export(tmp_path):
    body = (
        '$env:PORT = "4000"\n$env:API_TOKEN = "abc"\n'
        '$env:REGION = "<your-region>"\nnpm start'
    )
    root = _repo(tmp_path, "w", {"package.json": PKG, "README.md": _readme(body)})
    plan = _build(root)
    run = plan.steps[-1]
    assert run.env == {
        "PORT": "4000"
    }  # secret-like and placeholder values are not kept
    assert "abc" not in plan.model_dump_json()
    missing = [g.message for g in plan.gaps if g.kind == "missing_env"]
    assert any("API_TOKEN" in m for m in missing) and any(
        "REGION" in m for m in missing
    )
    assert not any("$env:" in t for t in _texts(plan))


def test_run_command_from_a_test_section_ranks_last(tmp_path):
    readme = (
        "# w\n\n## Running tests\n\n```bash\nnpm run e2e-serve\n```\n\n"
        "## Run\n\n```bash\nnpm start\n```\n"
    )
    pkg = '{"name": "w", "scripts": {"start": "x", "e2e-serve": "y"}}'
    root = _repo(tmp_path, "w", {"package.json": pkg, "README.md": readme})
    assert _texts(_build(root))[-1] == "npm start"
