"""T1.10: target resolution. Network-free: URL clones use file:// repos in tmp."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from rtfmup.source import (
    SourceError,
    clone_dir,
    is_url,
    repo_name,
    resolve,
)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("https://github.com/xb17/rtfmup", True),
        ("https://github.com/xb17/rtfmup.git", True),
        ("http://host/r.git", True),
        ("ssh://git@host/o/r.git", True),
        ("git://host/r.git", True),
        ("file:///tmp/repo", True),
        ("git@github.com:xb17/rtfmup.git", True),
        ("HTTPS://HOST/R", True),
        (".", False),
        ("..", False),
        ("C:\\Users\\me\\repo", False),
        ("C:/Users/me/repo", False),
        ("/home/me/repo", False),
        ("relative/dir", False),
        ("owner/repo", False),
        ("-oProxyCommand=evil", False),
        ("ext::sh -c evil", False),
    ],
)
def test_is_url(target, expected):
    assert is_url(target) is expected


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://github.com/xb17/rtfmup", "rtfmup"),
        ("https://github.com/xb17/rtfmup.git", "rtfmup"),
        ("https://github.com/xb17/rtfmup/", "rtfmup"),
        ("git@github.com:xb17/rtfmup.git", "rtfmup"),
        ("file:///tmp/My Repo.git", "My-Repo"),
        ("https://host/", "host"),
    ],
)
def test_repo_name(url, name):
    assert repo_name(url) == name


def test_clone_dir_is_stable_and_distinguishes_urls(tmp_path):
    a = clone_dir("https://github.com/a/repo", tmp_path)
    assert a == clone_dir("https://github.com/a/repo", tmp_path)
    assert a != clone_dir("https://github.com/b/repo", tmp_path)
    assert a.parent == tmp_path and a.name.startswith("repo-")


def test_local_path(tmp_path):
    src = resolve(str(tmp_path))
    assert src.root == tmp_path.resolve() and src.url is None and not src.cloned


def test_missing_local_path(tmp_path):
    with pytest.raises(SourceError, match="not a directory"):
        resolve(str(tmp_path / "nope"))


def _make_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "package.json").write_text("{}")

    def git(*a: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *a],
            cwd=remote, check=True, capture_output=True,
        )  # fmt: skip

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "one")
    return remote


def test_clone_then_reuse_and_update(tmp_path):
    remote = _make_remote(tmp_path)
    url = remote.as_uri()
    cache = tmp_path / "cache"
    messages: list[str] = []

    first = resolve(url, cache=cache, announce=messages.append)
    assert first.cloned and first.url == url
    assert (first.root / "package.json").is_file()
    assert first.root.parent == cache.resolve()
    assert any("Cloning" in m for m in messages)
    # a full clone, not shallow (D11)
    assert not (first.root / ".git" / "shallow").exists()

    (remote / "README.md").write_text("# new\n")
    subprocess.run(["git", "add", "-A"], cwd=remote, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "two"],
        cwd=remote, check=True, capture_output=True,
    )  # fmt: skip
    messages.clear()
    second = resolve(url, cache=cache, announce=messages.append)
    assert not second.cloned and second.root == first.root
    assert (second.root / "README.md").is_file()  # fast-forwarded
    assert any("cached clone" in m.lower() for m in messages)


def test_clone_failure_is_a_source_error(tmp_path):
    with pytest.raises(SourceError, match="git clone"):
        resolve(
            (tmp_path / "does-not-exist").as_uri(),
            cache=tmp_path / "cache",
            announce=lambda _: None,
        )


def test_clone_dest_that_is_not_a_repo_is_refused(tmp_path):
    url = "file:///nowhere/repo.git"
    dest = clone_dir(url, tmp_path / "cache")
    dest.mkdir(parents=True)
    with pytest.raises(SourceError, match="not a git clone"):
        resolve(url, cache=tmp_path / "cache", announce=lambda _: None)
