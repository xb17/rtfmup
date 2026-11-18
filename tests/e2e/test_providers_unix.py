"""T5.3b: real installs through the Linux and macOS providers.

These change the machine they run on, so each group runs ONLY where PLAN section 4.3 /
7.9 says, and only when the job names itself in `RTFMUP_PROVIDER_E2E`. Never set that
variable by hand on a developer machine. Opt in with `-m provider_e2e`.

  apt       the Gitea `linux` job's throwaway ubuntu container, running as root
            (.gitea/workflows/ci.yml, job provider-apt)
  mac-mini  mise and `uv python install` on the Mac mini as the runner's own
            non-admin user (job provider-mac-mini). Everything they install goes under
            the job's temp directory (RTFMUP_E2E_TMP): MISE_DATA_DIR, MISE_CACHE_DIR,
            MISE_CONFIG_DIR, MISE_STATE_DIR (mise docs, environment-variables) and
            UV_PYTHON_INSTALL_DIR, UV_PYTHON_BIN_DIR (uv docs, "Environment variables"),
            both checked 2026-11-18.
  brew      GitHub `macos-latest` only (.github/workflows/providers.yml): the Mac mini
            user is non-admin and cannot write to the Homebrew prefix.
"""

from __future__ import annotations

import io
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from rtfmup.execute.providers import all_providers, apt, select_steps
from rtfmup.execute.runner import run_step
from rtfmup.execute.unixpath import refreshed_path
from rtfmup.host.probe import probe_host
from rtfmup.host.ranges import parse_version

GROUP = os.environ.get("RTFMUP_PROVIDER_E2E", "")
pytestmark = pytest.mark.provider_e2e


def only(group: str, platform: str):
    return pytest.mark.skipif(
        GROUP != group or sys.platform != platform,
        reason=f"provider e2e group {group!r} on {platform} only",
    )


def run(step, cwd: Path, env: dict[str, str] | None = None, timeout: float = 1200):
    out, err = io.StringIO(), io.StringIO()
    result = run_step(step, cwd, timeout=timeout, env=env, out=out, err=err)
    return result, out.getvalue() + err.getvalue()


def summary(line: str) -> None:
    print(line)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with Path(target).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# --- apt: the Gitea linux container ------------------------------------------------


@pytest.fixture(scope="module")
def apt_lists(tmp_path_factory):
    """`apt-get update` through the provider's own prerequisite step."""
    host = probe_host()
    assert host.os == "linux" and apt.is_debian_family(host), host
    assert host.is_root, "the Gitea container runs as root; a sudo prompt would hang"
    cwd = tmp_path_factory.mktemp("apt")
    (step,) = apt.AptProvider().prerequisite_steps("php", None, host)
    assert step.argv == ["apt-get", "update"]  # root: no sudo
    result, text = run(step, cwd)
    assert result.ok, text
    return host


def _candidate(package: str) -> str | None:
    done = subprocess.run(
        ["apt-cache", "policy", package],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    m = re.search(r"^\s*Candidate:\s*(\S+)", done.stdout, re.MULTILINE)
    return None if not m or m.group(1) == "(none)" else m.group(1)


def _upstream(candidate: str) -> str:
    """`2:1.22~2build1` -> `1.22`; `18.19.1+dfsg-6ubuntu5` -> `18.19.1`."""
    candidate = re.sub(r"^\d+:", "", candidate)
    m = re.match(r"\d+(?:\.\d+)*", candidate)
    assert m, candidate
    return m.group()


@only("apt", "linux")
def test_every_apt_table_package_exists_at_least_at_the_table_version(apt_lists):
    """T5.3: the table is verified for real with `apt-cache policy`. The container's
    release is the older of the two LTS releases the table is written against."""
    rows = []
    for runtime, pkgs in sorted(apt.TABLE.items()):
        for pkg in pkgs:
            for name in (pkg.name, *pkg.also):
                cand = _candidate(name)
                assert cand, f"{runtime}: {name} has no candidate on this release"
                if name == pkg.name:
                    have, want = (
                        parse_version(_upstream(cand)),
                        parse_version(pkg.version),
                    )
                    assert have and want and have >= want, (name, cand, pkg.version)
                rows.append(f"{name} {cand}")
    summary("- apt-cache policy: " + "; ".join(rows))


@only("apt", "linux")
def test_apt_provider_installs_a_runtime_as_root(apt_lists, tmp_path):
    steps = select_steps("php", None, apt_lists)
    assert [s.id for s in steps] == ["apt-update", "runtime-php-apt"]
    result, text = run(steps[-1], tmp_path)
    assert result.ok, text
    check = subprocess.run(
        ["php", "--version"], capture_output=True, text=True, timeout=60, check=False
    )
    assert check.returncode == 0 and check.stdout.startswith("PHP 8."), check.stderr
    summary(f"- apt: installed php-cli ({check.stdout.splitlines()[0]})")


# --- the Mac mini, as its own non-admin user ---------------------------------------


def _mac_env() -> dict[str, str]:
    tmp = os.environ.get("RTFMUP_E2E_TMP")
    assert tmp and Path(tmp).is_dir(), "the job must set RTFMUP_E2E_TMP"
    root = Path(tmp)
    return {
        "MISE_DATA_DIR": str(root / "mise-data"),
        "MISE_CACHE_DIR": str(root / "mise-cache"),
        "MISE_CONFIG_DIR": str(root / "mise-config"),
        "MISE_STATE_DIR": str(root / "mise-state"),
        "UV_PYTHON_INSTALL_DIR": str(root / "uv-python"),
        "UV_PYTHON_BIN_DIR": str(root / "uv-bin"),
        "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"],
    }


@only("mac-mini", "darwin")
def test_mise_provider_installs_node_and_the_shim_is_found(monkeypatch, tmp_path):
    env = _mac_env()
    monkeypatch.setenv("PATH", env["PATH"])
    host = probe_host()
    assert host.tools["mise"].found, "the job installs a pinned mise first"
    steps = select_steps("node", "20.x", host)
    assert [s.argv for s in steps] == [["mise", "use", "--global", "node@20"]]
    result, text = run(steps[0], tmp_path, env)
    assert result.ok, text
    # `use --global` wrote to the redirected config dir, not to ~/.config/mise.
    config = Path(env["MISE_CONFIG_DIR"]) / "config.toml"
    assert config.is_file() and "node" in config.read_text(encoding="utf-8")
    # T5.2b: the shim directory is what puts `node` on PATH for the next step.
    path = refreshed_path(env["PATH"], installed=frozenset({"node"}), env=env)
    assert str(Path(env["MISE_DATA_DIR"]) / "shims") in path.split(os.pathsep)
    check = subprocess.run(
        ["node", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, **env, "PATH": path},
    )
    assert check.returncode == 0 and check.stdout.startswith("v20."), (
        check.stdout + check.stderr
    )
    summary(f"- mise: installed node {check.stdout.strip()} (shim on PATH)")


@only("mac-mini", "darwin")
def test_uv_python_provider_installs_into_the_job_directory(tmp_path):
    env = _mac_env()
    provider = all_providers()["uv_python"]
    step = provider.install_step("python", "3.13")
    assert step.argv == ["uv", "python", "install", "3.13"]
    result, text = run(step, tmp_path, env)
    assert result.ok, text
    installed = [p.name for p in Path(env["UV_PYTHON_INSTALL_DIR"]).iterdir()]
    assert any(n.startswith("cpython-3.13") for n in installed), installed
    summary(f"- uv_python: installed {installed}")


# --- brew: GitHub macos-latest only --------------------------------------------------


@only("brew", "darwin")
def test_brew_provider_installs_a_formula_and_it_lands_on_path(tmp_path):
    host = probe_host()
    assert host.tools["brew"].found and host.brew_prefix
    steps = select_steps("bun", None, host)
    assert [s.argv for s in steps] == [["brew", "install", "bun"]]
    result, text = run(steps[0], tmp_path)
    assert result.ok, text
    path = refreshed_path(os.environ["PATH"], installed=frozenset({"bun"}))
    check = subprocess.run(
        ["bun", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PATH": path},
    )
    assert check.returncode == 0, check.stdout + check.stderr
    summary(f"- brew: installed bun {check.stdout.strip()} under {host.brew_prefix}")
