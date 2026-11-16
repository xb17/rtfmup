"""README install alternatives: one system package manager, and missing tools as gaps.

From the T3.8 bench (ripgrep): an "Installation" section listing choco, scoop and
winget (and a dozen Linux and macOS managers) planned every Windows option as a step,
and the first one whose tool was not installed failed to start.
"""

from __future__ import annotations

from pathlib import Path

from hosts import windows_full, with_tool, without
from test_plan_rules import _build, _repo, _texts

ALTERNATIVES = """# tool

## Installation

If you're a macOS Homebrew user:

```sh
brew install tool
```

If you're a Windows Chocolatey user:

```powershell
choco install tool
```

If you're a Windows Scoop user:

```powershell
scoop install tool
```

If you're a Windows Winget user:

```powershell
winget install Vendor.Tool
```

If you're an Arch Linux user:

```sh
sudo pacman -S tool
```

If you're a Debian user:

```sh
sudo apt-get install tool
```
"""

SYSTEM_PMS = ("brew", "choco", "scoop", "winget", "pacman", "apt-get")


def _system_installs(plan) -> list[str]:
    return [t for t in _texts(plan) if any(pm in t.split()[:2] for pm in SYSTEM_PMS)]


def _plan(tmp_path: Path, host, readme: str = ALTERNATIVES):
    return _build(_repo(tmp_path, "tool", {"README.md": readme}), host=host)


def test_one_alternative_runs_the_first_installed_in_provider_order(tmp_path):
    host = with_tool(with_tool(windows_full(), "scoop", "0.5.3"), "choco", "2.5.1")
    plan = _plan(tmp_path, host)
    assert _system_installs(plan) == ["scoop install tool"]


def test_an_uninstalled_alternative_is_passed_over(tmp_path):
    host = with_tool(without(windows_full(), "scoop"), "choco", "2.5.1")
    plan = _plan(tmp_path, host)
    assert _system_installs(plan) == ["winget install Vendor.Tool"]


def test_other_os_alternatives_are_not_gaps_once_one_fits(tmp_path):
    host = with_tool(windows_full(), "scoop", "0.5.3")
    plan = _plan(tmp_path, host)
    assert not [g for g in plan.gaps if g.kind == "unsupported_platform"]
    assert [s for s in plan.steps if not s.platform_ok] == []


def test_same_manager_installs_in_a_section_all_run(tmp_path):
    readme = "# w\n\n## Setup\n\n```powershell\nwinget install A.A\nwinget install B.B\n```\n"
    plan = _plan(tmp_path, windows_full(), readme)
    assert [t for t in _texts(plan) if t.startswith("winget")] == [
        "winget install A.A",
        "winget install B.B",
    ]


def test_no_installed_alternative_is_one_missing_tool_gap(tmp_path):
    host = without(windows_full(), "scoop", "winget", "choco")
    plan = _plan(tmp_path, host)
    assert _system_installs(plan) == ["scoop install tool"]
    missing = [g for g in plan.gaps if g.kind == "missing_runtime"]
    assert len(missing) == 1
    assert "scoop" in missing[0].message and "not installed" in missing[0].message
    assert missing[0].fix and "scoop.sh" in missing[0].fix


def test_a_lone_system_install_whose_tool_is_missing_is_a_gap(tmp_path):
    readme = "# w\n\n## Setup\n\n```powershell\nchoco install tool\n```\n"
    plan = _plan(tmp_path, without(windows_full(), "choco"), readme)
    (gap,) = [g for g in plan.gaps if g.kind == "missing_runtime"]
    assert "choco" in gap.message and gap.fix and "chocolatey.org" in gap.fix


def test_without_a_host_alternative_other_os_ones_stay_gaps(tmp_path):
    readme = "# w\n\n## Install\n\n```sh\nbrew install tool\n```\n\n```sh\nsudo apt-get install tool\n```\n"
    plan = _plan(tmp_path, windows_full(), readme)
    assert len([g for g in plan.gaps if g.kind == "unsupported_platform"]) == 2
