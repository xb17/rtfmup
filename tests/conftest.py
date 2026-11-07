"""Shared test helpers. Fixtures are plain directories; git is added on demand."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=rtfmup-test",
            "-c",
            "user.email=test@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def fixture_copy(tmp_path: Path) -> Callable[..., Path]:
    """Copy tests/fixtures/<name> into tmp_path; optionally `git init` + commit."""

    def make(name: str, *, git: bool = False) -> Path:
        src = FIXTURES / name
        if not src.is_dir():
            raise FileNotFoundError(f"no such fixture: {name}")
        dest = tmp_path / name
        shutil.copytree(src, dest)
        if git:
            _git(dest, "init", "-q", "-b", "main")
            _git(dest, "add", "-A")
            _git(dest, "commit", "-q", "-m", "fixture")
        return dest

    return make
