"""Pick up PATH changes made by an install during this run (Linux and macOS).

The counterpart of `winpath.py`. A provider step (brew, mise, uv, rustup) puts a
program in a directory this process never had on its PATH, and nothing re-reads a
shell profile for a child. After a runtime step we prepend each well-known install
directory that exists and is not already on PATH, so the next step finds the tool.

Child processes only: this builds a PATH string for them and never edits a shell
profile, `.bashrc`, `.zshrc` or anything else (PLAN §7).

Directories, checked against their docs on 2026-11-18:
  * Homebrew: `<brew --prefix>/bin` and `/sbin` (docs.brew.sh/Manpage; never
    hard-coded, the prefix is asked of `brew`).
  * mise shims: `$MISE_SHIMS_DIR`, else `$MISE_DATA_DIR/shims`, else
    `$XDG_DATA_HOME/mise/shims`, else `~/.local/share/mise/shims`
    (mise.jdx.dev/directories.html and /dev-tools/shims.html). `mise activate
    --shims` prints the line that does the same in a shell profile; we do not run it.
  * cargo / rustup: `$CARGO_HOME/bin`, else `~/.cargo/bin` (the Cargo book,
    "Cargo home").
  * uv tool and Python shims: `$XDG_BIN_HOME`, else `~/.local/bin` (docs.astral.sh/uv,
    "Tools" > executable directory).
  * Go: `$(go env GOPATH)/bin`, asked of Go itself, and only when Go was installed
    in this run (it is the one directory that needs a subprocess).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path


def mise_shims_dir(env: Mapping[str, str], home: Path) -> Path:
    if env.get("MISE_SHIMS_DIR"):
        return Path(env["MISE_SHIMS_DIR"]).expanduser()
    if env.get("MISE_DATA_DIR"):
        return Path(env["MISE_DATA_DIR"]).expanduser() / "shims"
    if env.get("XDG_DATA_HOME"):
        return Path(env["XDG_DATA_HOME"]) / "mise" / "shims"
    return home / ".local" / "share" / "mise" / "shims"


def brew_prefix(path: str) -> str | None:
    """`brew --prefix` through `path`; None if brew is not there or fails."""
    from shutil import which

    exe = which("brew", path=path)
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe, "--prefix"],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip().splitlines()
    return out[0] if done.returncode == 0 and out else None


def go_path_bin(path: str) -> str | None:
    """`$(go env GOPATH)/bin` through `path`; None if go is not there or fails."""
    from shutil import which

    exe = which("go", path=path)
    if not exe:
        return None
    try:
        done = subprocess.run(
            [exe, "env", "GOPATH"],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = done.stdout.strip().splitlines()
    return os.path.join(out[0], "bin") if done.returncode == 0 and out else None


def candidate_dirs(
    *,
    env: Mapping[str, str],
    home: Path,
    brew: str | None,
    go_bin: str | None,
) -> list[str]:
    """The directories to consider, in the order they end up on PATH."""
    dirs: list[str] = []
    if brew:
        dirs += [os.path.join(brew, "bin"), os.path.join(brew, "sbin")]
    dirs.append(str(mise_shims_dir(env, home)))
    cargo = Path(env["CARGO_HOME"]).expanduser() if env.get("CARGO_HOME") else None
    dirs.append(str((cargo or home / ".cargo") / "bin"))
    dirs.append(env.get("XDG_BIN_HOME") or str(home / ".local" / "bin"))
    if go_bin:
        dirs.append(go_bin)
    return dirs


def refreshed_path(
    current: str,
    *,
    installed: frozenset[str] = frozenset(),
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
    exists: Callable[[str], bool] = os.path.isdir,
    brew: Callable[[str], str | None] = brew_prefix,
    go_bin: Callable[[str], str | None] = go_path_bin,
) -> str:
    """`current` with every existing, not-yet-present install directory in front.

    `installed` names the runtimes this run just installed; Go's bin directory is
    only added (and `go` only run) when `go` is among them. The result is stable
    (fixed order) and has no duplicates; running it twice changes nothing.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    have = {os.path.normpath(p) for p in current.split(os.pathsep) if p}
    dirs = candidate_dirs(
        env=env,
        home=home,
        brew=brew(current),
        go_bin=go_bin(current) if "go" in installed else None,
    )
    fresh: list[str] = []
    for d in dirs:
        norm = os.path.normpath(d)
        if norm in have or not exists(d):
            continue
        have.add(norm)
        fresh.append(d)
    return os.pathsep.join([*fresh, current]) if current else os.pathsep.join(fresh)
