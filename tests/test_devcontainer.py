"""T2.7: devcontainer export. Output is validated against the official schema."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import FIXTURES
from mini_jsonschema import Validator
from typer.testing import CliRunner

from rtfmup.analyze import analyze
from rtfmup.cli import app
from rtfmup.export.devcontainer import (
    BASE_IMAGE,
    FEATURES,
    build_devcontainer,
    linux_host,
)
from rtfmup.plan.build import Choices, build_plan

GOLDEN = Path(__file__).parent / "golden"
SCHEMA = json.loads(
    (Path(__file__).parent / "data" / "devContainer.base.schema.json").read_text(
        encoding="utf-8"
    )
)
VALIDATOR = Validator(SCHEMA)
runner = CliRunner()

CASES: dict[str, tuple[str, Choices]] = {
    "py-uv": ("py-uv", Choices()),
    "node-pnpm": ("node-pnpm", Choices()),
    "fullstack-compose": ("fullstack-compose", Choices()),
    "unix-only": ("unix-only", Choices()),
    "windows-readme": ("windows-readme", Choices()),
    "docker-or-native.native": ("docker-or-native", Choices(variant="native")),
    "docker-or-native.docker": ("docker-or-native", Choices(variant="docker")),
    "polyglot-server-client.client": (
        "polyglot-server-client",
        Choices(component="client"),
    ),
    "rust-cli": ("rust-cli", Choices()),
    "go-cli": ("go-cli", Choices()),
    "dotnet-web": ("dotnet-web", Choices()),
}


def _doc(tmp_path: Path, fixture: str, choices: Choices) -> dict:
    root = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, root)
    a = analyze(root)
    plan = build_plan(a, linux_host(), choices)
    return build_devcontainer(a, plan, root=root)


@pytest.mark.parametrize("case", sorted(CASES))
def test_export_is_schema_valid_and_matches_golden(tmp_path, case):
    fixture, choices = CASES[case]
    doc = _doc(tmp_path, fixture, choices)
    assert VALIDATOR.errors(doc) == []
    assert "image" in doc or "dockerComposeFile" in doc  # the schema allows neither
    actual = json.dumps(doc, indent=2) + "\n"
    path = GOLDEN / f"{case}.devcontainer.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == actual


def test_runtimes_become_features_with_the_manifest_version(tmp_path):
    doc = _doc(tmp_path, "node-pnpm", Choices())
    assert doc["features"] == {FEATURES["node"]: {"version": "20"}}
    assert doc["image"] == BASE_IMAGE
    assert doc["forwardPorts"] == [3000]


def test_install_env_setup_build_chain_in_order_without_the_run_step(tmp_path):
    doc = _doc(tmp_path, "node-pnpm", Choices())
    assert doc["postCreateCommand"] == (
        "pnpm install && (test -f .env || cp .env.example .env)"
    )
    assert "pnpm dev" not in doc["postCreateCommand"]
    notes = doc["customizations"]["rtfmup"]["notes"]
    assert "Start the app with: pnpm dev" in notes


def test_uv_is_bootstrapped_and_says_so(tmp_path):
    doc = _doc(tmp_path, "py-uv", Choices())
    assert doc["postCreateCommand"].startswith("pip install uv && uv sync")
    assert any(
        "uv is installed with pip" in n
        for n in doc["customizations"]["rtfmup"]["notes"]
    )


def test_service_ports_are_not_forwarded_as_app_ports(tmp_path):
    doc = _doc(tmp_path, "fullstack-compose", Choices())
    assert doc["forwardPorts"] == [3000]  # not postgres 5432 / redis 6379


def test_steps_for_windows_are_left_out_and_listed(tmp_path):
    doc = _doc(tmp_path, "windows-readme", Choices())
    assert "Activate.ps1" not in doc.get("postCreateCommand", "")
    notes = " ".join(doc["customizations"]["rtfmup"]["notes"])
    assert "Left out (needs another OS)" in notes and "Activate.ps1" in notes


def test_unix_steps_are_included_and_high_risk_ones_flagged(tmp_path):
    doc = _doc(tmp_path, "unix-only", Choices())
    cmd = doc["postCreateCommand"]
    assert "sudo apt install" in cmd and "source venv/bin/activate" in cmd
    notes = " ".join(doc["customizations"]["rtfmup"]["notes"])
    assert "Review before building" in notes and "pipes a download" in notes


def test_component_steps_run_in_their_directory(tmp_path):
    doc = _doc(tmp_path, "polyglot-server-client", Choices(component="client"))
    assert "(cd client && npm ci)" in doc["postCreateCommand"]


def test_docker_variant_with_one_app_service_uses_compose(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "README.md").write_text(
        "# a\n\n## Docker\n\n```bash\ndocker compose up\n```\n\n"
        "## Native\n\n```bash\npython app.py\n```\n"
    )
    (root / "compose.yaml").write_text(
        "services:\n  web:\n    build: .\n    ports: ['8000:8000']\n"
        "  db:\n    image: postgres:17\n"
    )
    a = analyze(root)
    plan = build_plan(a, linux_host(), Choices(variant="docker"))
    doc = build_devcontainer(a, plan, root=root)
    assert VALIDATOR.errors(doc) == []
    assert doc["dockerComposeFile"] == "../compose.yaml" and doc["service"] == "web"
    assert "image" not in doc and "workspaceFolder" in doc


def test_docker_variant_with_an_ambiguous_service_falls_back_to_an_image(tmp_path):
    root = tmp_path / "two"
    root.mkdir()
    (root / "README.md").write_text(
        "# a\n\n## Docker\n\n```bash\ndocker compose up\n```\n\n"
        "## Native\n\n```bash\npython app.py\n```\n"
    )
    (root / "compose.yaml").write_text(
        "services:\n  web:\n    build: .\n  worker:\n    build: .\n"
    )
    a = analyze(root)
    doc = build_devcontainer(
        a, build_plan(a, linux_host(), Choices(variant="docker")), root=root
    )
    assert VALIDATOR.errors(doc) == []
    assert doc["image"] == BASE_IMAGE and "dockerComposeFile" not in doc
    assert any(
        "no single app service" in n for n in doc["customizations"]["rtfmup"]["notes"]
    )


def test_unversioned_runtime_gets_the_feature_default(tmp_path):
    doc = _doc(tmp_path, "go-cli", Choices())
    assert FEATURES["go"] in doc["features"]


def test_export_is_deterministic(tmp_path):
    a = json.dumps(_doc(tmp_path / "a", "fullstack-compose", Choices()))
    b = json.dumps(_doc(tmp_path / "b", "fullstack-compose", Choices()))
    assert a == b


def test_secret_values_never_appear(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    (root / "package.json").write_text('{"name":"w","scripts":{"start":"x"}}')
    (root / "README.md").write_text(
        "# w\n\n## Setup\n\n```bash\nexport API_TOKEN=hunter2\nnpm start\n```\n"
    )
    a = analyze(root)
    doc = build_devcontainer(a, build_plan(a, linux_host(), Choices()), root=root)
    assert "hunter2" not in json.dumps(doc)


# --- CLI ----------------------------------------------------------------------------


@pytest.fixture
def interactive(monkeypatch):
    monkeypatch.setattr("rtfmup.cli._is_interactive", lambda: True)


def _copy(tmp_path: Path, name: str, git: bool = False) -> Path:
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root)
    if git:
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            ["commit", "-qm", "x"],
        ):
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *args],
                cwd=root, check=True, capture_output=True,
            )  # fmt: skip
    return root


def test_cli_prints_valid_json_and_saves_it_under_dot_rtfmup(tmp_path):
    root = _copy(tmp_path, "fullstack-compose", git=True)
    result = runner.invoke(app, ["export", "devcontainer", str(root)])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.stdout)
    assert VALIDATOR.errors(doc) == []
    saved = root / ".rtfmup" / "devcontainer.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == doc
    assert not (root / ".devcontainer").exists()  # never into the repo by default
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status.strip() == ""


def test_write_creates_the_repo_file_after_confirmation(tmp_path, interactive):
    root = _copy(tmp_path, "py-uv")
    result = runner.invoke(
        app, ["export", "devcontainer", str(root), "--write"], input="y\n"
    )
    assert result.exit_code == 0, result.output
    written = root / ".devcontainer" / "devcontainer.json"
    assert json.loads(written.read_text(encoding="utf-8"))["image"] == BASE_IMAGE
    assert b"\r\n" not in written.read_bytes()


def test_write_declined_writes_nothing_and_exits_3(tmp_path, interactive):
    root = _copy(tmp_path, "py-uv")
    result = runner.invoke(
        app, ["export", "devcontainer", str(root), "--write"], input="n\n"
    )
    assert result.exit_code == 3
    assert not (root / ".devcontainer").exists()


@pytest.mark.parametrize(
    "existing", [".devcontainer/devcontainer.json", ".devcontainer.json"]
)
def test_write_refuses_to_overwrite(tmp_path, interactive, existing):
    root = _copy(tmp_path, "py-uv")
    target = root / existing
    target.parent.mkdir(exist_ok=True)
    target.write_text('{"image": "mine"}', encoding="utf-8")
    result = runner.invoke(
        app, ["export", "devcontainer", str(root), "--write"], input="y\n"
    )
    assert result.exit_code == 2 and "refusing to overwrite" in result.output
    assert target.read_text(encoding="utf-8") == '{"image": "mine"}'


def test_write_needs_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr("rtfmup.cli._is_interactive", lambda: False)
    root = _copy(tmp_path, "py-uv")
    result = runner.invoke(app, ["export", "devcontainer", str(root), "--write"])
    assert result.exit_code == 2 and "needs a terminal" in result.output
    assert not (root / ".devcontainer").exists()


def test_cli_asks_for_the_variant_without_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr("rtfmup.cli._is_interactive", lambda: False)
    root = _copy(tmp_path, "docker-or-native")
    result = runner.invoke(app, ["export", "devcontainer", str(root)])
    assert result.exit_code == 2 and "--variant" in result.output
