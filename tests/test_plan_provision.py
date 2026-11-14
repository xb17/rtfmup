"""M3: missing runtimes become provider install steps, ahead of everything else."""

from __future__ import annotations

import shutil
from pathlib import Path

from hosts import linux_full, windows_full, with_tool, without

from rtfmup.analyze import analyze
from rtfmup.plan.build import Choices, build_plan

FIXTURES = Path(__file__).parent / "fixtures"


def plan_for(tmp_path: Path, name: str, host):
    dest = tmp_path / name
    shutil.copytree(FIXTURES / name, dest)
    return build_plan(analyze(dest), host, Choices())


def kinds(plan, kind):
    return [g for g in plan.gaps if g.kind == kind]


def test_missing_runtime_becomes_an_install_step_not_a_gap(tmp_path):
    plan = plan_for(tmp_path, "go-cli", without(windows_full(), "go"))
    runtime = [s for s in plan.steps if s.kind == "runtime"]
    assert [s.id for s in runtime] == ["runtime-go-winget"]
    assert runtime[0].argv and runtime[0].argv[:2] == ["winget", "install"]
    assert not kinds(plan, "missing_runtime")


def test_runtime_steps_come_first(tmp_path):
    plan = plan_for(tmp_path, "no-readme-node", without(windows_full(), "node", "npm"))
    assert plan.steps[0].kind == "runtime"
    assert all(s.kind != "runtime" for s in plan.steps[1:])


def test_provider_order_prefers_user_scope(tmp_path):
    host = with_tool(without(windows_full(), "go"), "scoop", "0.5.3")
    plan = plan_for(tmp_path, "go-cli", host)
    assert [s.id for s in plan.steps if s.kind == "runtime"] == ["runtime-go-scoop"]


def test_unsatisfiable_pin_stays_a_gap(tmp_path):
    # node-pnpm pins Node 20; no verified package provides it, so nothing is
    # installed on a guess.
    plan = plan_for(tmp_path, "node-pnpm", without(windows_full(), "node", "npm"))
    assert not [s for s in plan.steps if s.kind == "runtime"]
    (gap,) = kinds(plan, "missing_runtime")
    assert gap.fix and "nodejs.org" in gap.fix


def test_no_provider_tool_means_a_gap(tmp_path):
    host = without(windows_full(), "go", "winget", "uv")
    plan = plan_for(tmp_path, "go-cli", host)
    assert not [s for s in plan.steps if s.kind == "runtime"]
    assert kinds(plan, "missing_runtime")


def test_other_hosts_keep_gaps_until_m5(tmp_path):
    plan = plan_for(tmp_path, "go-cli", without(linux_full(), "go"))
    assert not [s for s in plan.steps if s.kind == "runtime"]
    assert kinds(plan, "missing_runtime")


def test_one_install_step_per_runtime(tmp_path):
    plan = plan_for(tmp_path, "py-requirements", without(windows_full(), "python"))
    ids = [s.id for s in plan.steps if s.kind == "runtime"]
    assert len(ids) == len(set(ids)) == 1
