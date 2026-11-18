"""T4.0: install alternatives. One route per component; nothing is dropped silently."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hosts import windows_full, with_tool, without
from test_plan_rules import _build, _readme, _repo, _texts

from rtfmup.plan.render import render_plan

GOLDEN = Path(__file__).parent / "golden"

NO_CARGO_NO_MANAGERS = ("cargo", "rustc", "winget", "scoop", "choco")


def _hosts():
    base = without(windows_full(), "scoop")
    return {
        # cargo and winget found, no scoop: winget is high risk, cargo install medium
        "cargo": base,
        # scoop found too: `scoop install` is rated medium (T2.3, "installs a
        # toolchain"), the same as `cargo install`, so README order decides and the
        # earlier Scoop section wins
        "scoop": with_tool(base, "scoop", "0.5.3"),
        # no cargo, but winget fits: a system manager that fits beats a missing cargo
        "no-cargo": without(base, "cargo", "rustc"),
        # no cargo and no package manager at all: still `cargo install` (rtfmup can
        # provision a toolchain, never a package manager); the rust runtime gap shows
        "bare": without(windows_full(), *NO_CARGO_NO_MANAGERS),
    }


OWN_URL = "https://github.com/demo/altdemo"


def _alt_plan(fixture_copy, case: str):
    """The install-alternatives fixture as a checkout of its own repo."""
    root = fixture_copy("install-alternatives")
    return _build(root, host=_hosts()[case], own_urls=[OWN_URL])


def _stable(plan) -> dict:
    data = json.loads(plan.model_dump_json())
    data.pop("analysis_ref")
    return data


@pytest.mark.parametrize("case", sorted(_hosts()))
def test_golden_install_alternatives(fixture_copy, case):
    plan = _alt_plan(fixture_copy, case)
    actual = json.dumps(_stable(plan), indent=2) + "\n"
    path = GOLDEN / f"install-alternatives.{case}.plan.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == actual


def _install_steps(plan) -> list[str]:
    return [
        " ".join(s.argv) if s.argv else (s.shell_text or "")
        for s in plan.steps
        if s.kind in ("install", "setup", "build")
    ]


def test_host_with_cargo_and_no_scoop_plans_cargo_install_only(fixture_copy):
    plan = _alt_plan(fixture_copy, "cargo")
    assert _install_steps(plan) == ["cargo install altdemo"]
    skipped = {s.text: s.reason for s in plan.skipped_alternatives}
    # every other README command is listed, with the route that won
    assert set(skipped) == {
        "winget install Demo.altdemo",
        "scoop install altdemo",
        "cargo binstall altdemo",
        "curl -LO https://github.com/demo/altdemo/releases/download/1.0.0/altdemo_1.0.0_amd64.deb",
        "sudo dpkg -i altdemo_1.0.0_amd64.deb",
        "cargo build --release",
        "cargo build --release --features x",
        "rustup target add x86_64-unknown-linux-musl",
        "cargo build --release --target x86_64-unknown-linux-musl",
    }
    assert (
        "route cargo install chosen: tool found, medium risk"
        in skipped["winget install Demo.altdemo"]
    )
    assert all(
        s.evidence and s.evidence[0].line_start for s in plan.skipped_alternatives
    )


def test_scoop_ties_cargo_on_risk_and_wins_on_readme_order(fixture_copy):
    plan = _alt_plan(fixture_copy, "scoop")
    assert _install_steps(plan) == ["scoop install altdemo"]
    (scoop,) = [s for s in plan.steps if s.kind == "install"]
    assert scoop.risk == "medium"  # T2.3: scoop installs are medium, same as cargo


def test_without_cargo_a_fitting_system_manager_wins(fixture_copy):
    plan = _alt_plan(fixture_copy, "no-cargo")
    assert _install_steps(plan) == ["winget install Demo.altdemo"]


def test_without_cargo_or_managers_the_route_is_still_cargo_install(fixture_copy):
    plan = _alt_plan(fixture_copy, "bare")
    assert _install_steps(plan) == ["cargo install altdemo"]
    assert any(g.kind == "missing_runtime" for g in plan.gaps)


def test_binstall_wins_only_when_the_probe_finds_it_and_it_ranks_higher(fixture_copy):
    # found: cargo-binstall is rated medium like cargo install, so README order keeps
    # `cargo install` first
    host = with_tool(_hosts()["cargo"], "cargo-binstall", "1.27.0")
    plan = _build(fixture_copy("install-alternatives"), host=host, own_urls=[OWN_URL])
    assert _install_steps(plan) == ["cargo install altdemo"]


def test_rendered_plan_lists_alternatives_not_chosen(fixture_copy):
    from io import StringIO

    from rich.console import Console

    plan = _alt_plan(fixture_copy, "cargo")
    out = StringIO()
    render_plan(plan, Console(file=out, width=200, force_terminal=False))
    text = out.getvalue()
    assert "Alternatives not chosen" in text
    assert text.index("Steps") < text.index("Alternatives not chosen")
    assert "winget install Demo.altdemo" in text


# --- negatives: not self-delivering, or no alternative to choose ------------------------


def test_pip_install_requirements_is_not_self_delivering(fixture_copy):
    plan = _build(fixture_copy("py-requirements"))
    assert plan.skipped_alternatives == []
    assert any("pip install -r requirements.txt" in t for t in _texts(plan))


def test_pnpm_install_and_build_stay(fixture_copy):
    plan = _build(fixture_copy("node-pnpm"))
    assert plan.skipped_alternatives == []


def test_dependency_install_stays_next_to_the_project_install(tmp_path):
    root = _repo(
        tmp_path,
        "dep",
        {
            "Cargo.toml": '[package]\nname = "altdemo"\nversion = "1.0.0"\n',
            "README.md": _readme("brew install postgresql\ncargo install altdemo"),
        },
    )
    plan = _build(root, host=_hosts()["cargo"])
    texts = _install_steps(plan)
    assert "cargo install altdemo" in texts
    assert any(t.startswith("brew install postgresql") for t in _texts(plan))
    assert plan.skipped_alternatives == []


def test_a_single_route_keeps_everything(tmp_path):
    root = _repo(
        tmp_path,
        "one",
        {
            "Cargo.toml": '[package]\nname = "solo"\nversion = "1.0.0"\n',
            "README.md": _readme("cargo build --release\ncargo run"),
        },
    )
    plan = _build(root, host=_hosts()["cargo"])
    assert plan.skipped_alternatives == []


def test_variant_builds_keep_the_shortest_and_the_fence_mates_go(tmp_path):
    readme = (
        "# solo\n\n## Building\n\n```bash\ncargo build --release\n```\n\n"
        "```bash\ncargo build --release --features pcre2\n```\n\n"
        "```bash\nrustup target add x86_64-unknown-linux-musl\n"
        "cargo build --release --target x86_64-unknown-linux-musl\n```\n"
    )
    root = _repo(
        tmp_path,
        "var",
        {
            "Cargo.toml": '[package]\nname = "solo"\nversion = "1.0.0"\n',
            "README.md": readme,
        },
    )
    plan = _build(root, host=_hosts()["cargo"])
    assert "cargo build --release" in _texts(plan)
    assert not any("pcre2" in t or "musl" in t for t in _texts(plan))
    assert {s.text for s in plan.skipped_alternatives} == {
        "cargo build --release --features pcre2",
        "rustup target add x86_64-unknown-linux-musl",
        "cargo build --release --target x86_64-unknown-linux-musl",
    }
