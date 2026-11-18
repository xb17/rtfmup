"""T5.3 provider tests for Linux and macOS: selection, steps, risk. No installs."""

from __future__ import annotations

import pytest
from hosts import arch_linux, fedora, mac, ubuntu, with_tools, without

from rtfmup.execute.confirm import is_runnable
from rtfmup.execute.providers import (
    LINUX_ORDER,
    MACOS_ORDER,
    WINDOWS_ORDER,
    all_providers,
    select_steps,
)
from rtfmup.execute.providers import apt as apt_mod
from rtfmup.execute.providers import brew as brew_mod
from rtfmup.execute.providers import dnf as dnf_mod
from rtfmup.execute.providers import mise as mise_mod
from rtfmup.execute.providers import pacman as pacman_mod
from rtfmup.execute.providers.apt import AptProvider
from rtfmup.execute.providers.brew import BrewProvider
from rtfmup.execute.providers.dnf import DnfProvider
from rtfmup.execute.providers.mise import MiseProvider
from rtfmup.execute.providers.pacman import PacmanProvider
from rtfmup.host.probe import HostProfile
from rtfmup.plan.risk import rate


def argvs(steps):
    return [s.argv for s in steps]


# --- order (D12) ---------------------------------------------------------------


def test_orders_are_fixed_per_os_with_mise_in_d12_position():
    assert WINDOWS_ORDER[:3] == ("uv_python", "mise", "scoop")
    assert MACOS_ORDER == ("uv_python", "mise", "brew", "manual")
    assert LINUX_ORDER == ("uv_python", "mise", "apt", "dnf", "pacman", "manual")


# --- availability: a provider is used only when its tool is installed -------------


@pytest.mark.parametrize(
    ("provider", "host_ok", "host_other"),
    [
        (AptProvider(), ubuntu(), fedora()),
        (DnfProvider(), fedora(), ubuntu()),
        (PacmanProvider(), arch_linux(), ubuntu()),
        (BrewProvider(), with_tools(mac(), "brew"), mac()),
    ],
    ids=["apt", "dnf", "pacman", "brew"],
)
def test_distro_manager_needs_its_distro_and_its_tool(provider, host_ok, host_other):
    assert provider.available(host_ok)
    assert not provider.available(host_other)


def test_apt_is_not_available_without_the_tool_or_a_way_to_elevate():
    assert not AptProvider().available(without(ubuntu(), "apt"))
    no_sudo = without(ubuntu(), "sudo")
    assert not AptProvider().available(no_sudo)  # not root, no sudo
    assert AptProvider().available(
        without(ubuntu(root=True), "sudo")
    )  # root needs none


def test_derivatives_are_recognised_through_id_like():
    mint = ubuntu().model_copy(update={"distro_id": "linuxmint"})
    assert AptProvider().available(mint)
    rocky = fedora().model_copy(
        update={"distro_id": "rocky", "distro_like": ["rhel", "fedora"]}
    )
    assert DnfProvider().available(rocky)
    manjaro = arch_linux().model_copy(
        update={"distro_id": "manjaro", "distro_like": ["arch"]}
    )
    assert PacmanProvider().available(manjaro)


def test_brew_is_macos_only_and_mise_is_everywhere():
    assert not BrewProvider().available(with_tools(ubuntu(), "brew"))
    for h in (ubuntu(), mac(), fedora()):
        assert not MiseProvider().available(h)
        assert MiseProvider().available(with_tools(h, "mise"))


# --- sudo only when not root ----------------------------------------------------------


def test_apt_steps_use_sudo_only_when_not_root():
    user = select_steps("node", None, ubuntu())
    assert argvs(user) == [
        ["sudo", "apt-get", "update"],
        [
            "sudo",
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            "nodejs",
            "npm",
        ],
    ]
    root = select_steps("node", None, ubuntu(root=True))
    assert argvs(root)[0] == ["apt-get", "update"]
    assert "sudo" not in root[1].argv


def test_unknown_uid_is_treated_as_not_root():
    host = ubuntu().model_copy(update={"is_root": None})
    assert select_steps("go", None, host)[1].argv[0] == "sudo"


def test_dnf_and_pacman_commands():
    assert argvs(select_steps("go", None, fedora())) == [
        ["sudo", "dnf", "install", "-y", "golang"]
    ]
    assert argvs(select_steps("rust", None, fedora(root=True))) == [
        ["dnf", "install", "-y", "rust", "cargo"]
    ]
    assert argvs(select_steps("node", None, arch_linux())) == [
        ["sudo", "pacman", "-S", "--needed", "--noconfirm", "nodejs", "npm"]
    ]


def test_brew_and_mise_commands():
    h = with_tools(mac(), "brew")
    assert argvs(select_steps("node", None, h)) == [["brew", "install", "node"]]
    h = with_tools(mac(), "brew", "mise")
    assert argvs(select_steps("node", "20.x", h)) == [
        ["mise", "use", "--global", "node@20"]
    ]


# --- risk ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "runtime", "risk"),
    [
        (ubuntu(), "node", "high"),
        (ubuntu(root=True), "node", "high"),  # high either way
        (fedora(), "go", "high"),
        (arch_linux(), "go", "high"),
        (with_tools(mac(), "brew"), "node", "medium"),
        (with_tools(mac(), "mise"), "node", "medium"),
        (with_tools(ubuntu(), "mise"), "node", "medium"),
    ],
)
def test_risk_per_provider(host, runtime, risk):
    steps = select_steps(runtime, None, host)
    assert steps and all(s.risk == risk for s in steps)
    assert all(s.risk_reasons for s in steps)
    assert all(is_runnable(s) for s in steps)


def test_system_package_steps_are_high_by_the_rate_rules_too():
    """The step's own risk is authoritative, but the rules agree for sudo."""
    sudo_step = select_steps("node", None, ubuntu())[1]
    level, _ = rate(" ".join(sudo_step.argv))
    assert level == "high"


def test_mise_states_the_global_config_write():
    step = select_steps("node", None, with_tools(mac(), "mise"))[0]
    assert any("global config" in r for r in step.risk_reasons)


# --- selection ----------------------------------------------------------------------------


def test_python_prefers_uv_then_mise_then_the_system_manager():
    h = with_tools(ubuntu(), "mise", "uv")
    first = argvs(select_steps("python", ">=3.11", h))[0]
    assert first[:3] == ["uv", "python", "install"]
    h = without(h, "uv")
    assert argvs(select_steps("python", ">=3.11", h)) == [
        ["mise", "use", "--global", "python@3.15"]
    ]
    h = without(h, "mise")
    assert argvs(select_steps("python", ">=3.11", h))[1][-1] == "python3"


def test_a_spec_the_table_cannot_prove_is_not_promised():
    # apt's nodejs is 18.x on the oldest supported LTS: >=20 cannot be promised.
    assert [s.kind for s in select_steps("node", ">=20", ubuntu())] == ["manual"]
    assert select_steps("node", ">=18", ubuntu())[-1].kind == "runtime"
    # ...but mise can serve it.
    assert argvs(select_steps("node", ">=20", with_tools(ubuntu(), "mise"))) == [
        ["mise", "use", "--global", "node@lts"]
    ]


def test_mise_picks_the_release_line_for_an_npm_style_spec():
    h = with_tools(ubuntu(), "mise")
    assert select_steps("node", "^22.0.0", h)[0].argv[-1] == "node@22"
    assert select_steps("node", "18.x", h)[0].argv[-1] == "node@18"
    # a go.mod `go 1.24` is a minimum, so the newest line serves it
    assert select_steps("go", "1.24", h)[0].argv[-1] == "go@1.27"
    assert select_steps("java", None, h) == []


def test_apt_update_has_one_id_for_every_runtime():
    ids = {s.id for r in ("node", "go") for s in select_steps(r, None, ubuntu())}
    assert "apt-update" in ids  # shared, so the plan keeps one
    assert len(ids) == 3  # update + two installs


def test_manual_is_the_last_resort_for_known_runtimes():
    bare = HostProfile(os="linux", arch="x86_64", tools={})
    steps = select_steps("node", None, bare)
    assert [s.kind for s in steps] == ["manual"]


def test_install_step_rejects_what_it_cannot_provide():
    with pytest.raises(ValueError):
        AptProvider().install_step("bun", None, ubuntu())
    with pytest.raises(ValueError):
        BrewProvider().install_step("python", None, mac())


# --- the tables --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table",
    [
        apt_mod.TABLE,
        dnf_mod.TABLE,
        pacman_mod.TABLE,
        brew_mod.TABLE,
        mise_mod.TABLE,
    ],
    ids=["apt", "dnf", "pacman", "brew", "mise"],
)
def test_every_table_entry_has_a_name_and_a_parsable_version(table):
    from rtfmup.host.ranges import parse_version

    for runtime, pkgs in table.items():
        assert pkgs, runtime
        for pkg in pkgs:
            assert pkg.name and parse_version(pkg.version), (runtime, pkg)


def test_registry_has_every_provider_once():
    names = list(all_providers())
    assert len(names) == len(set(names))
    assert {"mise", "brew", "apt", "dnf", "pacman"} <= set(names)


# --- architecture awareness ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("runtime", "spec", "arch", "os_", "has_gap"),
    [
        ("node", "14.x", "arm64", "macos", True),
        ("node", ">=10 <16", "arm64", "macos", True),
        ("node", "14.x", "x86_64", "macos", False),
        ("node", "14.x", "arm64", "linux", False),  # linux arm64 builds go back to v4
        ("node", ">=14", "arm64", "macos", False),  # 16+ satisfies it
        ("node", "20.x", "arm64", "macos", False),
        ("node", None, "arm64", "macos", False),
        ("go", "1.15", "arm64", "macos", False),  # go.mod `go` is a minimum
    ],
)
def test_no_build_for_this_cpu(runtime, spec, arch, os_, has_gap):
    from rtfmup.host.archlimits import no_build_for_arch

    host = HostProfile(os=os_, arch=arch, tools={})
    assert bool(no_build_for_arch(runtime, spec, host)) is has_gap


def test_a_pinned_old_node_on_apple_silicon_is_a_gap_not_a_failing_install(tmp_path):
    from rtfmup.analyze import analyze
    from rtfmup.plan.build import build_plan

    (tmp_path / "package.json").write_text(
        '{"name": "x", "engines": {"node": "14.x"}}', encoding="utf-8"
    )
    arm = without(with_tools(mac("arm64"), "brew", "mise"), "node")
    plan = build_plan(analyze(tmp_path), arm)
    gaps = [g for g in plan.gaps if g.kind == "runtime_version"]
    assert gaps and "no macos arm64 build" in gaps[0].message
    assert "16.0.0" in gaps[0].message
    assert not any(s.kind == "runtime" for s in plan.steps)  # nothing to fail
    # on Intel there is no arch gap (the manual fix still applies)
    intel = without(with_tools(mac("x86_64"), "mise"), "node")
    plan = build_plan(analyze(tmp_path), intel)
    assert not any("no macos" in g.message for g in plan.gaps)


# --- T5.3 accept, as a plan: node-pnpm needs node >=20 -------------------------------


def _plan(host):
    from conftest import FIXTURES

    from rtfmup.analyze import analyze
    from rtfmup.plan.build import build_plan

    return build_plan(analyze(FIXTURES / "node-pnpm"), host)


def _runtime_argvs(plan):
    return [s.argv for s in plan.steps if s.kind == "runtime"]


def test_node_pnpm_gets_a_mise_step_when_node_is_missing_on_linux():
    plan = _plan(without(with_tools(ubuntu(), "mise"), "node"))
    # .nvmrc pins 20, so the release line is 20, not the newest LTS
    assert _runtime_argvs(plan) == [["mise", "use", "--global", "node@20"]]
    assert not any(g.kind == "missing_runtime" for g in plan.gaps)


def test_node_pnpm_on_a_mac_picks_mise_and_does_not_misuse_brew_for_node_20():
    """.nvmrc pins 20; brew's `node` is 26 and node@20 is keg-only and disabled."""
    plan = _plan(without(with_tools(mac(), "brew"), "node"))
    assert _runtime_argvs(plan) == []
    assert any(g.kind == "missing_runtime" for g in plan.gaps)
    plan = _plan(without(with_tools(mac(), "brew", "mise"), "node"))
    assert _runtime_argvs(plan) == [["mise", "use", "--global", "node@20"]]


def test_node_pnpm_on_ubuntu_without_mise_cannot_promise_node_20_from_apt():
    """apt's nodejs is 18 on 24.04: it stays a gap with the manual fix, not a step."""
    plan = _plan(without(ubuntu(), "node"))
    assert _runtime_argvs(plan) == []
    assert any(g.kind == "missing_runtime" for g in plan.gaps)


def test_node_pnpm_with_node_present_has_no_provider_step():
    from hosts import linux_full

    assert _runtime_argvs(_plan(linux_full())) == []
