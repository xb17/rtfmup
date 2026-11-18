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


def test_ubuntu_uses_apt_get_not_an_unprobed_gentoo_emerge(tmp_path):
    """T5.8 on Linux: ripgrep's README lists emerge before apt-get; Ubuntu has apt-get."""
    from hosts import ubuntu, with_tools

    readme = (
        "# tool\n\n## Installation\n\n```sh\nsudo emerge sys-apps/tool\n```\n\n"
        "```sh\nsudo apt-get install tool\n```\n"
    )
    plan = _plan(tmp_path, with_tools(ubuntu(), "apt-get"), readme)
    texts = [s.title for s in plan.steps if s.kind == "install"]
    assert any("apt-get install tool" in t for t in texts), texts
    assert not any("emerge" in t for t in texts), texts


def test_an_unprobed_system_manager_does_not_beat_cargo(tmp_path):
    """Unprobed used to rank above missing, so `emerge` beat a cargo rtfmup provides."""
    from hosts import ubuntu

    readme = (
        "# tool\n\n## Installation\n\n```sh\nsudo emerge sys-apps/tool\n```\n\n"
        "```sh\ncargo install tool\n```\n"
    )
    cargo_toml = '[package]\nname = "tool"\nversion = "1.0.0"\n'
    root = _repo(tmp_path, "tool", {"README.md": readme, "Cargo.toml": cargo_toml})
    # cargo probed and missing (rtfmup can provision rust); emerge not probed at all
    plan = _build(root, host=without(ubuntu(), "cargo"))
    texts = [s.title for s in plan.steps if s.kind == "install"]
    assert any("cargo install tool" in t for t in texts), texts
    assert not any("emerge" in t for t in texts), texts


def test_macos_with_an_unwritable_brew_falls_back_from_brew_install(tmp_path):
    """T5.8 on the Mac mini: `brew install datasette` failed on brew's lock files."""
    from hosts import mac, with_tool

    readme = (
        "# tool\n\n## Installation\n\n```sh\nbrew install tool\n```\n\n"
        "```sh\npip install tool\n```\n"
    )
    host = mac().model_copy(
        update={
            "tools": {
                **mac().tools,
                "brew": mac().tools.get("brew") or _missing("brew"),
            }
        }
    )
    plan = _plan(tmp_path, with_tool(host, "python", "3.12.11"), readme)
    texts = [s.title for s in plan.steps if s.kind == "install"]
    assert not any(t.startswith("brew install") for t in texts), texts


def _missing(name: str):
    from rtfmup.host.probe import ToolInfo

    return ToolInfo(
        found=False,
        path=f"/opt/homebrew/bin/{name}",
        note="installed, but /opt/homebrew is not writable by this user",
    )
