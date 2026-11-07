"""T2.4: gaps between a host and a working setup."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from hosts import docker_down, windows_full, with_tool, without
from test_plan import FIXTURES

from rtfmup.analyze import analyze
from rtfmup.plan.build import Choices, build_plan

KINDS = [
    "missing_runtime",
    "runtime_version",
    "missing_env",
    "external_account",
    "service",
    "unsupported_platform",
    "unresolved",
]


def _gaps(tmp_path: Path, name: str, host, choices=None):
    dest = tmp_path / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURES / name, dest)
    return build_plan(analyze(dest), host, choices or Choices()).gaps


def _of(gaps, kind):
    return [g for g in gaps if g.kind == kind]


def test_missing_node_is_one_gap_with_a_manual_fix(tmp_path):
    gaps = _gaps(tmp_path, "node-pnpm", without(windows_full(), "node", "npm"))
    missing = _of(gaps, "missing_runtime")
    assert len(missing) == 1  # npm is part of Node: no second gap
    assert "Node.js" in missing[0].message and "not installed" in missing[0].message
    assert missing[0].fix == "Install Node.js from https://nodejs.org/en/download"
    assert not _of(gaps, "runtime_version")  # nothing to compare against


def test_old_node_is_a_version_gap_naming_what_is_installed(tmp_path):
    host = with_tool(windows_full(), "node", "16.20.2")
    (gap,) = _of(_gaps(tmp_path, "node-pnpm", host), "runtime_version")[:1]
    assert "16.20.2" in gap.message and gap.fix and "nodejs.org" in gap.fix


def test_any_installed_version_that_satisfies_is_enough(tmp_path):
    host = with_tool(windows_full(), "node", "26.13.0", "20.11.1")
    assert not _of(_gaps(tmp_path, "node-pnpm", host), "runtime_version")


def test_unverifiable_spec_is_reported_not_guessed(tmp_path):
    dest = tmp_path / "w"
    dest.mkdir()
    (dest / "package.json").write_text('{"name":"w","scripts":{"start":"x"}}')
    (dest / ".nvmrc").write_text("lts/iron\n")
    gaps = build_plan(analyze(dest), windows_full()).gaps
    (gap,) = _of(gaps, "runtime_version")
    assert "cannot verify" in gap.message and gap.fix is None


def test_uv_project_does_not_need_a_system_python(tmp_path):
    host = without(windows_full(), "python")
    gaps = _gaps(tmp_path, "py-uv", host)
    assert not _of(gaps, "missing_runtime") and not _of(gaps, "runtime_version")


def test_python_missing_without_uv_is_a_gap_for_python_and_uv(tmp_path):
    host = without(windows_full(), "python", "uv")
    messages = " ".join(
        g.message for g in _of(_gaps(tmp_path, "py-uv", host), "missing_runtime")
    )
    assert "Python" in messages and "uv" in messages


def test_package_manager_missing(tmp_path):
    gaps = _gaps(tmp_path, "node-pnpm", without(windows_full(), "pnpm"))
    (gap,) = _of(gaps, "missing_runtime")
    assert (
        "pnpm" in gap.message
        and gap.fix == "Install pnpm: https://pnpm.io/installation"
    )


def test_rust_channel_is_not_a_version_gap(tmp_path):
    dest = tmp_path / "r"
    shutil.copytree(FIXTURES / "rust-cli", dest)
    (dest / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "stable"\n')
    gaps = build_plan(analyze(dest), windows_full()).gaps
    assert not _of(gaps, "runtime_version")
    # but a missing toolchain is
    gone = build_plan(analyze(dest), without(windows_full(), "rustc", "cargo")).gaps
    assert _of(gone, "missing_runtime")


def test_docker_variant_needs_a_running_daemon(tmp_path):
    choices = Choices(variant="docker")
    up = _gaps(tmp_path, "docker-or-native", windows_full(), choices)
    assert not _of(up, "service") and not _of(up, "missing_runtime")
    down = _gaps(
        tmp_path / "d", "docker-or-native", docker_down(windows_full()), choices
    )
    (gap,) = _of(down, "service")
    assert "daemon" in gap.message
    gone = _gaps(
        tmp_path / "e", "docker-or-native", without(windows_full(), "docker"), choices
    )
    assert any("docker" in g.message.lower() for g in _of(gone, "missing_runtime"))


def test_native_path_flags_compose_services_docker_path_does_not(tmp_path):
    native = _gaps(
        tmp_path, "docker-or-native", windows_full(), Choices(variant="native")
    )
    assert [g.message.split()[0] for g in _of(native, "service")] == ["postgres"]
    docker = _gaps(
        tmp_path / "x", "docker-or-native", windows_full(), Choices(variant="docker")
    )
    assert not _of(docker, "service")


def test_required_env_lists_names_only(tmp_path):
    gaps = _gaps(tmp_path, "fullstack-compose", windows_full())
    (gap,) = _of(gaps, "missing_env")
    assert "SECRET_KEY" in gap.message and "STRIPE_API_KEY" in gap.message
    assert "=" not in gap.message


def test_external_account_gap_points_at_the_signup_url(tmp_path):
    gaps = _gaps(tmp_path, "fullstack-compose", windows_full())
    (gap,) = _of(gaps, "external_account")
    assert gap.fix and gap.fix.startswith("https://")


def test_gaps_are_grouped_by_kind_in_a_stable_order(tmp_path):
    for name, choices in (
        ("fullstack-compose", None),
        ("unix-only", None),
        ("node-pnpm", None),
    ):
        host = with_tool(without(windows_full(), "pnpm"), "node", "16.0.0")
        gaps = _gaps(tmp_path / name, name, host, choices)
        idx = [KINDS.index(g.kind) for g in gaps]
        assert idx == sorted(idx), name


def test_steps_for_another_os_are_gaps_on_windows(tmp_path):
    gaps = _gaps(tmp_path, "unix-only", windows_full())
    kinds = {g.kind for g in gaps}
    assert "unsupported_platform" in kinds
    assert any("apt" in g.message for g in _of(gaps, "unsupported_platform"))


@pytest.mark.parametrize("name", ["empty", "py-uv", "go-cli"])
def test_fully_equipped_host_has_no_runtime_gaps(tmp_path, name):
    gaps = _gaps(tmp_path, name, windows_full())
    assert not _of(gaps, "missing_runtime") and not _of(gaps, "runtime_version")
