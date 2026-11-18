"""T5.4: the macos-readme and linux-readme fixtures planned for macOS and Linux hosts.

Each host keeps only its own OS's install section: a macOS host runs the macOS
section and nothing from the Linux or Windows ones, and a Debian, Fedora or Arch host
runs the apt, dnf or pacman line of the Linux section and none of the others. Steps
that cannot run on the host stay listed as `unsupported_platform` gaps (D7).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from hosts import arch_linux, fedora, mac, ubuntu, with_tools

from rtfmup.analyze import analyze
from rtfmup.plan.build import build_plan

GOLDEN = Path(__file__).parent / "golden"

HOSTS = {
    "mac": lambda: with_tools(mac(), "brew"),
    "ubuntu": ubuntu,
    "fedora": fedora,
    "arch": arch_linux,
}
CASES = [
    ("macos-readme", "mac"),
    ("macos-readme", "ubuntu"),
    ("macos-readme", "fedora"),
    ("linux-readme", "mac"),
    ("linux-readme", "ubuntu"),
    ("linux-readme", "fedora"),
    ("linux-readme", "arch"),
]


def _plan(fixture_copy, fixture: str, host: str):
    return build_plan(analyze(fixture_copy(fixture)), HOSTS[host]())


def _texts(plan) -> list[str]:
    return [
        " ".join(s.argv) if s.argv else (s.shell_text or s.title)
        for s in plan.steps
        if s.platform_ok
    ]


@pytest.mark.parametrize(("fixture", "host"), CASES)
def test_golden_plan_per_host(fixture_copy, fixture, host):
    plan = _plan(fixture_copy, fixture, host)
    data = json.loads(plan.model_dump_json())
    data.pop("analysis_ref")  # hashes the absolute tmp path
    actual = json.dumps(data, indent=2) + "\n"
    path = GOLDEN / f"{fixture}.on-{host}.plan.json"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == actual


def test_a_mac_runs_the_macos_section_only(fixture_copy):
    texts = _texts(_plan(fixture_copy, "macos-readme", "mac"))
    assert "xcode-select --install" in texts and "brew install node@20" in texts
    assert not any("apt" in t or "winget" in t for t in texts)


@pytest.mark.parametrize("host", ["ubuntu", "fedora", "arch"])
def test_a_linux_host_runs_the_linux_section_only(fixture_copy, host):
    texts = _texts(_plan(fixture_copy, "macos-readme", host))
    assert "sudo apt install nodejs npm" in texts
    assert not any("brew" in t or "xcode-select" in t or "winget" in t for t in texts)


def test_each_distro_runs_its_own_package_manager_line(fixture_copy):
    expected = {
        "ubuntu": "sudo apt install postgresql",
        "fedora": "sudo dnf install postgresql-server",
        "arch": "sudo pacman -S postgresql",
    }
    root = fixture_copy("linux-readme")
    for host, line in expected.items():
        texts = _texts(build_plan(analyze(root), HOSTS[host]()))
        assert line in texts, (host, texts)
        others = [v for k, v in expected.items() if k != host]
        assert not any(o in texts for o in others), (host, texts)
        assert "sudo systemctl start postgresql" in texts


def test_a_mac_cannot_run_the_linux_readme_and_says_so(fixture_copy):
    plan = _plan(fixture_copy, "linux-readme", "mac")
    texts = _texts(plan)
    assert not any("apt" in t or "systemctl" in t for t in texts)
    gaps = [g.message for g in plan.gaps if g.kind == "unsupported_platform"]
    assert any("systemctl" in m for m in gaps)
    assert any("macos" in m for m in gaps)
