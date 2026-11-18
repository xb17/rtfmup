"""T3.3 provider tests: selection, step generation, risk. No real installs."""

from __future__ import annotations

import pytest
from hosts import windows_full, without

from rtfmup.execute.confirm import is_runnable
from rtfmup.execute.providers import WINDOWS_ORDER, all_providers, select_steps
from rtfmup.execute.providers import choco as choco_mod
from rtfmup.execute.providers import scoop as scoop_mod
from rtfmup.execute.providers import winget as winget_mod
from rtfmup.execute.providers.choco import ChocoProvider
from rtfmup.execute.providers.manual import ManualProvider
from rtfmup.execute.providers.scoop import ScoopProvider
from rtfmup.execute.providers.uv_python import UvPythonProvider
from rtfmup.execute.providers.winget import WingetProvider
from rtfmup.host.probe import ToolInfo
from rtfmup.plan.risk import rate


def host(**tools: bool):
    """windows_full with scoop/choco present or absent as requested."""
    h = windows_full()
    t = dict(h.tools)
    for name in ("scoop", "choco"):
        t[name] = ToolInfo(found=tools.get(name, False), path="/x")
    return h.model_copy(update={"tools": t})


def scoop_provider(*buckets: str) -> ScoopProvider:
    return ScoopProvider(buckets=lambda: set(buckets))


# --- availability ---------------------------------------------------------


def test_provider_is_used_only_when_its_tool_is_installed():
    h = host()
    assert WingetProvider().available(h)
    assert UvPythonProvider().available(h)
    assert not ScoopProvider().available(h)
    assert not ChocoProvider().available(h)
    assert not WingetProvider().available(without(h, "winget"))
    assert not UvPythonProvider().available(without(h, "uv"))
    assert ManualProvider().available(without(h, "winget", "uv"))


# --- selection order (D12) -------------------------------------------------


def test_default_order_prefers_user_scope_providers():
    assert WINDOWS_ORDER == (
        "uv_python",
        "mise",
        "scoop",
        "winget",
        "choco",
        "manual",
    )


def test_python_goes_to_uv_first():
    steps = select_steps("python", ">=3.11", host(scoop=True, choco=True))
    assert [s.argv[:3] for s in steps] == [["uv", "python", "install"]]


def test_node_prefers_scoop_over_winget_over_choco():
    h = host(scoop=True, choco=True)
    providers = {**all_providers(), "scoop": scoop_provider("main", "versions")}
    steps = select_steps("node", None, h, providers=providers)
    assert steps[-1].id == "runtime-node-scoop"
    steps = select_steps("node", None, without(h, "scoop"), providers=providers)
    assert steps[-1].id == "runtime-node-winget"
    h2 = without(without(h, "scoop"), "winget")
    steps = select_steps("node", None, h2, providers=providers)
    assert steps[-1].id == "runtime-node-choco"


def test_custom_order_is_honored_and_unknown_names_ignored():
    steps = select_steps(
        "go", None, host(choco=True), order=("nope", "choco", "winget")
    )
    assert steps[-1].id == "runtime-go-choco"


def test_unprovidable_runtime_falls_through_to_manual():
    h = without(host(), "uv", "winget")
    steps = select_steps("go", None, h)
    assert [s.kind for s in steps] == ["manual"]
    assert "go.dev/dl" in steps[0].title
    assert not is_runnable(steps[0])


def test_nothing_available_and_no_manual_page_gives_no_steps():
    assert select_steps("java", None, host()) == []


def test_unmatched_spec_falls_through_instead_of_installing_the_wrong_thing():
    # winget only has Node 26; a Node 20 pin must not resolve to it.
    h = without(host(), "uv")
    steps = select_steps("node", "20.x", h)
    assert [s.kind for s in steps] == ["manual"]


# --- winget ----------------------------------------------------------------


def test_winget_python_is_user_scope_medium():
    step = WingetProvider().install_step("python", ">=3.12,<3.14")
    assert step.id == "runtime-python-winget"
    assert "Python.Python.3.13" in step.argv  # newest line satisfying the range
    assert step.argv[step.argv.index("--scope") + 1] == "user"
    assert step.risk == "medium"
    assert "--disable-interactivity" in step.argv
    assert step.argv[step.argv.index("--source") + 1] == "winget"


def test_winget_without_user_installer_is_high_risk():
    step = WingetProvider().install_step("go", None)
    assert "--scope" not in step.argv
    assert step.risk == "high"
    assert "elevation" in step.risk_reasons[0]


def test_winget_node_defaults_to_lts():
    assert "OpenJS.NodeJS.LTS" in WingetProvider().install_step("node", None).argv


def test_winget_cannot_provide_rust_or_unmatched_specs():
    p = WingetProvider()
    assert not p.can_provide("rust", None)
    assert not p.can_provide("python", "<3.0")
    assert not p.can_provide("java", None)
    with pytest.raises(ValueError):
        p.install_step("rust", None)


# --- scoop -----------------------------------------------------------------


def test_scoop_main_package_needs_no_bucket_step():
    p = scoop_provider()
    assert p.prerequisite_steps("bun", None) == []
    step = p.install_step("bun", None)
    assert step.argv == ["scoop", "install", "bun"]
    assert step.risk == "medium"


def test_scoop_versions_bucket_adds_its_own_medium_step():
    p = scoop_provider("main")
    pre = p.prerequisite_steps("node", "20.x")
    assert [s.argv for s in pre] == [["scoop", "bucket", "add", "versions"]]
    assert pre[0].risk == "medium"
    assert p.install_step("node", "20.x").argv == [
        "scoop",
        "install",
        "versions/nodejs20",
    ]


def test_scoop_skips_bucket_step_when_present():
    assert scoop_provider("main", "versions").prerequisite_steps("node", "20.x") == []


def test_select_steps_orders_bucket_before_install():
    h = without(host(scoop=True), "uv")
    providers = {**all_providers(), "scoop": scoop_provider("main")}
    steps = select_steps("node", "22.x", h, providers=providers)
    assert [s.id for s in steps] == ["scoop-bucket-versions", "runtime-node-scoop"]


def test_installed_buckets_reads_scoop_root(tmp_path, monkeypatch):
    (tmp_path / "buckets" / "main").mkdir(parents=True)
    (tmp_path / "buckets" / "Versions").mkdir()
    (tmp_path / "buckets" / "stray.txt").write_text("x")
    monkeypatch.setenv("SCOOP", str(tmp_path))
    assert scoop_mod.installed_buckets() == {"main", "versions"}
    monkeypatch.setenv("SCOOP", str(tmp_path / "missing"))
    assert scoop_mod.installed_buckets() == set()


# --- choco -----------------------------------------------------------------


def test_choco_is_always_high_risk():
    for runtime in choco_mod.TABLE:
        step = ChocoProvider().install_step(runtime, None)
        assert step.risk == "high", runtime
        assert step.argv[:2] == ["choco", "install"]
        assert step.argv[-2:] == ["-y", "--no-progress"]


def test_choco_pins_python_by_package_name():
    assert ChocoProvider().install_step("python", "3.12.*").argv[2] == "python312"


# --- uv_python -------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (None, ["uv", "python", "install"]),
        ("3.12", ["uv", "python", "install", "3.12"]),
        ("3.12.3", ["uv", "python", "install", "3.12.3"]),
        (">=3.11", ["uv", "python", "install", ">=3.11"]),
        (">=3.8,<3.10", ["uv", "python", "install", ">=3.8,<3.10"]),
    ],
)
def test_uv_python_requests(spec, expected):
    step = UvPythonProvider().install_step("python", spec)
    assert step.argv == expected
    assert step.risk == "medium"


def test_uv_python_scope():
    p = UvPythonProvider()
    assert not p.can_provide("node", None)
    assert not p.can_provide("python", "latest-ish")
    with pytest.raises(ValueError):
        p.install_step("python", "not a spec")


# --- manual ----------------------------------------------------------------


def test_manual_step_is_not_runnable_and_names_the_page():
    step = ManualProvider().install_step("rust", None)
    assert step.kind == "manual" and step.argv is None and step.shell_text is None
    assert "rustup.rs" in step.title


# --- cross-cutting ---------------------------------------------------------


def _every_step():
    for module in (winget_mod, scoop_mod, choco_mod):
        prov = {
            winget_mod: WingetProvider(),
            scoop_mod: scoop_provider(),
            choco_mod: ChocoProvider(),
        }[module]
        for runtime in module.TABLE:
            yield [
                *prov.prerequisite_steps(runtime, None),
                prov.install_step(runtime, None),
            ]
    yield [UvPythonProvider().install_step("python", None)]


def test_provider_risk_never_understates_the_plan_risk_classifier():
    """The generic classifier (plan/risk.py) rates what the README would show."""
    order = {"low": 0, "medium": 1, "high": 2}
    for steps in _every_step():
        for s in steps:
            text = " ".join(s.argv)
            generic, _ = rate(text)
            assert order[s.risk] >= order[generic], (text, s.risk, generic)


def test_every_table_entry_is_well_formed():
    for mod in (winget_mod, scoop_mod, choco_mod):
        for runtime, pkgs in mod.TABLE.items():
            assert pkgs, runtime
            for pkg in pkgs:
                assert pkg.name and pkg.version[0].isdigit(), (mod.__name__, pkg)
                assert " " not in pkg.name
