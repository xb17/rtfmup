"""Fetch pinned benchmark repos into the cache dir (never into this repo).

Read-only: `git fetch` of one commit. Nothing from the fetched repositories is
executed. Usage: `uv run python bench/fetch.py [name ...]`.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from platformdirs import user_cache_dir

HERE = Path(__file__).parent


def load_corpus(path: Path | None = None) -> list[dict]:
    with (path or HERE / "corpus.toml").open("rb") as fh:
        return tomllib.load(fh)["repo"]


def bench_cache() -> Path:
    return Path(user_cache_dir("rtfmup", appauthor=False)) / "bench"


def checkout_dir(repo: dict, cache: Path | None = None) -> Path:
    return (cache or bench_cache()) / f"{repo['name']}-{repo['sha'][:10]}"


def _git(args: list[str], cwd: Path) -> None:
    # A freshly `git init`-ed directory has no hooks, and fetched content can't add any.
    cmd = ["git", "-c", "protocol.ext.allow=never", *args]
    # Output stays attached to the terminal: live progress.
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd} (exit {proc.returncode})"
        )


def ensure_checkout(repo: dict, cache: Path | None = None) -> Path:
    """A work tree of exactly `repo['sha']` (shallow: the benchmark needs no history)."""
    dest = checkout_dir(repo, cache)
    if (dest / ".git").is_dir():
        _ensure_origin_and_tags(repo, dest)
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"Fetching {repo['name']} @ {repo['sha'][:10]} -> {dest}",
        file=sys.stderr,
        flush=True,
    )
    _git(["init", "-q"], dest)
    _git(
        [
            "fetch",
            "--depth",
            "1",
            "--progress",
            "--",
            repo["url"] + ".git",
            repo["sha"],
        ],
        dest,
    )
    _git(["-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD"], dest)
    _ensure_origin_and_tags(repo, dest)
    return dest


def _ensure_origin_and_tags(repo: dict, dest: Path) -> None:
    """Give the checkout the `origin` a real clone has, and tags when the repo needs them.

    rtfmup recognises "the clone we are already in" by remote URL, and a README step
    such as `git checkout <tag>` needs the tag to exist (`fetch_tags = true`).
    """
    url = repo["url"] + ".git"
    have = subprocess.run(
        ["git", "remote"], cwd=dest, capture_output=True, text=True, check=False
    ).stdout.split()
    if "origin" not in have:
        _git(["remote", "add", "origin", url], dest)
    if repo.get("fetch_tags"):
        tags = subprocess.run(
            ["git", "tag", "--list"],
            cwd=dest,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.split()
        if not tags:
            print(f"Fetching tags for {repo['name']}", file=sys.stderr, flush=True)
            _git(["fetch", "--depth", "1", "--tags", "--progress", "origin"], dest)


def main(argv: list[str]) -> int:
    wanted = set(argv)
    for repo in load_corpus():
        if not wanted or repo["name"] in wanted:
            print(ensure_checkout(repo))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
