"""Turn a target (local path or Git URL) into a local directory to analyze.

URL targets are cloned with plain `git clone` into the cache dir (D9, D11): no
`--depth`, and git's own progress output is streamed to the user. Cloning never
runs anything from the repository.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_cache_dir

_URL = re.compile(r"^(?:https?|ssh|git|file)://", re.IGNORECASE)
_SCP = re.compile(
    r"^[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:(?!//)\S+$"
)  # git@github.com:owner/repo.git
_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class SourceError(Exception):
    """The target could not be turned into a local directory."""


@dataclass
class Source:
    root: Path
    url: str | None = None  # set for URL targets
    cloned: bool = False  # True if this call performed a fresh clone


def is_url(target: str) -> bool:
    return bool(_URL.match(target) or _SCP.match(target))


def repo_name(url: str) -> str:
    """`https://host/owner/repo.git` -> `repo` (the name `git clone` would pick)."""
    tail = re.split(r"[/:]", url.rstrip("/"))[-1]
    tail = tail.removesuffix(".git")
    return _NAME.sub("-", tail).strip("-.") or "repo"


def cache_root() -> Path:
    return Path(user_cache_dir("rtfmup", appauthor=False)) / "clones"


def clone_dir(url: str, cache: Path | None = None) -> Path:
    """Stable per-URL directory: `<name>-<hash of the exact URL>`."""
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return (cache or cache_root()) / f"{repo_name(url)}-{digest}"


def _git(args: list[str], *, cwd: Path | None = None) -> None:
    """Run git with its output attached to the terminal (live progress)."""
    cmd = ["git", "-c", "protocol.ext.allow=never", *args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, check=False)
    except FileNotFoundError as exc:
        raise SourceError("git is not installed or not on PATH") from exc
    if proc.returncode != 0:
        raise SourceError(f"`git {args[0]}` failed (exit {proc.returncode})")


def resolve(
    target: str,
    *,
    cache: Path | None = None,
    announce: Callable[[str], None] | None = None,
) -> Source:
    """Return a Source for a local path or URL. Raises SourceError on failure."""
    say = announce or (lambda msg: print(msg, file=sys.stderr, flush=True))
    if not is_url(target):
        path = Path(target).expanduser()
        if not path.is_dir():
            raise SourceError(f"not a directory: {path.resolve()}")
        return Source(root=path.resolve())

    if target.startswith(
        "-"
    ):  # cannot happen via is_url, but never pass options to git
        raise SourceError(f"invalid target: {target}")
    dest = clone_dir(target, cache)
    if (dest / ".git").is_dir():
        say(f"Using cached clone: {dest}")
        say("Updating (git pull --ff-only)")
        _git(["pull", "--ff-only", "--progress"], cwd=dest)
        return Source(root=dest.resolve(), url=target)

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SourceError(f"cache path exists but is not a git clone: {dest}")
    say(f"Cloning {target}\n  into {dest}")
    _git(["clone", "--progress", "--", target, str(dest)])
    return Source(root=dest.resolve(), url=target, cloned=True)
