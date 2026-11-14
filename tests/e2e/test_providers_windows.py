"""T3.3b: install one small runtime through each Windows provider, for real.

These change the machine they run on. They run on ephemeral GitHub
`windows-latest` runners ONLY (`.github/workflows/providers.yml`), never locally:
the module refuses to run anywhere else. Opt in with `-m provider_e2e`.

A provider whose tool is missing on the runner is skipped and listed in the
summary, so the job reports what was and was not exercised.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rtfmup.execute.providers import all_providers
from rtfmup.execute.runner import run_step

pytestmark = [
    pytest.mark.provider_e2e,
    pytest.mark.skipif(sys.platform != "win32", reason="Windows providers"),
    pytest.mark.skipif(
        os.environ.get("GITHUB_ACTIONS") != "true",
        reason="provider e2e installs software: ephemeral GitHub runners only",
    ),
]

# (provider, runtime, spec, command that proves the package is installed)
CASES = {
    "uv_python": ("python", "3.13", ["uv", "python", "find", "3.13"]),
    "scoop": ("bun", None, ["scoop", "list", "bun"]),
    "winget": (
        "bun",
        None,
        [
            "winget",
            "list",
            "--exact",
            "--id",
            "Oven-sh.Bun",
            "--accept-source-agreements",
        ],
    ),
    "choco": ("bun", None, ["choco", "list", "--exact", "bun", "--local-only"]),
}
TOOL = {"uv_python": "uv", "scoop": "scoop", "winget": "winget", "choco": "choco"}


def summary(line: str) -> None:
    print(line)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with Path(target).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def test_record_which_tools_the_image_has():
    """T3.3b asks to record whether Chocolatey (and the rest) is preinstalled."""
    for tool in ("uv", "scoop", "winget", "choco"):
        found = shutil.which(tool)
        summary(
            f"- {tool}: {'preinstalled at ' + found if found else 'NOT preinstalled'}"
        )


@pytest.mark.parametrize("name", sorted(CASES))
def test_provider_installs_a_runtime(name, tmp_path):
    runtime, spec, verify = CASES[name]
    if not shutil.which(TOOL[name]):
        pytest.skip(f"{TOOL[name]} is not installed on this runner")
    provider = all_providers()[name]
    steps = [
        *provider.prerequisite_steps(runtime, spec),
        provider.install_step(runtime, spec),
    ]
    for i, step in enumerate(steps, 1):
        out, err = io.StringIO(), io.StringIO()
        result = run_step(
            step, tmp_path, index=i, total=len(steps), timeout=900, out=out, err=err
        )
        assert result.ok, f"{step.title}\n{out.getvalue()}\n{err.getvalue()}"
    check = subprocess.run(
        verify, capture_output=True, text=True, timeout=120, check=False
    )
    assert check.returncode == 0, check.stdout + check.stderr
    summary(f"- {name}: installed {runtime} ({' '.join(steps[-1].argv or [])})")
