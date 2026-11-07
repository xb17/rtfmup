"""Plan builder: golden plans per fixture, plus targeted rule tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hosts import windows_full

from rtfmup.analyze import analyze
from rtfmup.plan.build import Choices, build_plan

GOLDEN = Path(__file__).parent / "golden"
FIXTURES = Path(__file__).parent / "fixtures"

# fixture -> choices needed to answer its ambiguities
GOLDEN_CASES: dict[str, Choices] = {
    "py-uv": Choices(),
    "py-requirements": Choices(),
    "node-pnpm": Choices(),
    "no-readme-node": Choices(),
    "docker-or-native@native": Choices(variant="native"),
    "docker-or-native@docker": Choices(variant="docker"),
    "polyglot-server-client@server": Choices(component="server"),
    "polyglot-server-client@client": Choices(component="client"),
    "fullstack-compose": Choices(),
    "unix-only": Choices(),
    "windows-readme": Choices(),
    "rust-cli": Choices(),
    "go-cli": Choices(),
    "dotnet-web": Choices(),
    "stale-readme": Choices(),
    "empty": Choices(),
}


def _plan(fixture_copy, name: str, choices: Choices, host=None, own_urls=()):
    root = fixture_copy(name)
    return build_plan(analyze(root), host or windows_full(), choices, own_urls=own_urls)


def _stable(plan) -> dict:
    data = json.loads(plan.model_dump_json())
    data.pop("analysis_ref")  # hashes the absolute tmp path
    return data


@pytest.mark.parametrize("case", sorted(GOLDEN_CASES))
def test_golden_plan(fixture_copy, case):
    name = case.split("@")[0]
    plan = _plan(fixture_copy, name, GOLDEN_CASES[case])
    actual = json.dumps(_stable(plan), indent=2) + "\n"
    path = GOLDEN / f"{case.replace('@', '.')}.plan.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == actual
