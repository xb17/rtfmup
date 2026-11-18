"""T5.2b: PATH after provider installs on Linux and macOS. Fake directories only.

The module is pure string work, so these also run on Windows; paths are compared
normalized and joined with os.pathsep.
"""

from __future__ import annotations

import os
from pathlib import Path

from rtfmup.execute.unixpath import candidate_dirs, mise_shims_dir, refreshed_path

HOME = Path("/home/u")
BREW = "/opt/homebrew"
SEP = os.pathsep


def n(path: str) -> str:
    return os.path.normpath(path)


ALL = {
    n(f"{BREW}/bin"),
    n(f"{BREW}/sbin"),
    n("/home/u/.local/share/mise/shims"),
    n("/home/u/.cargo/bin"),
    n("/home/u/.local/bin"),
    n("/home/u/go/bin"),
}


def run(current: str, existing=ALL, *, brew=None, go=None, installed=frozenset()):
    return refreshed_path(
        current,
        installed=installed,
        env={},
        home=HOME,
        exists=lambda d: n(d) in existing,
        brew=lambda _p: brew,
        go_bin=lambda _p: go,
    )


def split(path: str) -> list[str]:
    return [n(p) for p in path.split(SEP) if p]


def test_existing_directories_come_first_in_the_documented_order():
    out = run(
        f"/usr/bin{SEP}/bin",
        brew=BREW,
        go="/home/u/go/bin",
        installed=frozenset({"go"}),
    )
    assert split(out) == [
        n(f"{BREW}/bin"),
        n(f"{BREW}/sbin"),
        n("/home/u/.local/share/mise/shims"),
        n("/home/u/.cargo/bin"),
        n("/home/u/.local/bin"),
        n("/home/u/go/bin"),
        n("/usr/bin"),
        n("/bin"),
    ]


def test_missing_directories_are_not_added():
    out = run("/usr/bin", {n("/home/u/.cargo/bin")}, brew=BREW)
    assert split(out) == [n("/home/u/.cargo/bin"), n("/usr/bin")]


def test_directories_already_on_path_are_not_duplicated_or_moved():
    current = SEP.join(["/usr/bin", "/home/u/.cargo/bin/", f"{BREW}/bin"])
    out = split(run(current, brew=BREW))
    assert out.count(n("/home/u/.cargo/bin")) == 1
    assert out.count(n(f"{BREW}/bin")) == 1
    assert out[-3:] == [n("/usr/bin"), n("/home/u/.cargo/bin"), n(f"{BREW}/bin")]
    assert len(out) == len(set(out))


def test_it_is_stable_running_twice_changes_nothing():
    once = run("/usr/bin", brew=BREW)
    assert run(once, brew=BREW) == once


def test_go_is_only_consulted_when_go_was_just_installed():
    asked: list[str] = []

    def go_bin(path: str) -> str:
        asked.append(path)
        return "/home/u/go/bin"

    def call(installed=frozenset()):
        return refreshed_path(
            "/usr/bin",
            installed=installed,
            env={},
            home=HOME,
            exists=lambda d: n(d) in ALL,
            brew=lambda _p: None,
            go_bin=go_bin,
        )

    assert n("/home/u/go/bin") not in split(call()) and asked == []
    assert n("/home/u/go/bin") in split(call(frozenset({"go"})))
    assert asked == ["/usr/bin"]


def test_no_brew_means_no_homebrew_directories():
    assert not any("homebrew" in d for d in split(run("/usr/bin", brew=None)))


def test_empty_path_gives_just_the_new_directories():
    assert run("", {n("/home/u/.local/bin")}) == str(Path("/home/u/.local/bin"))


def test_mise_shims_dir_follows_mise_variables():
    assert mise_shims_dir({}, HOME) == HOME / ".local/share/mise/shims"
    assert mise_shims_dir({"XDG_DATA_HOME": "/x"}, HOME) == Path("/x/mise/shims")
    assert mise_shims_dir({"MISE_DATA_DIR": "/d"}, HOME) == Path("/d/shims")
    both = {"MISE_DATA_DIR": "/d", "MISE_SHIMS_DIR": "/s"}
    assert mise_shims_dir(both, HOME) == Path("/s")


def test_cargo_home_and_xdg_bin_home_are_honoured():
    env = {"CARGO_HOME": "/c", "XDG_BIN_HOME": "/b"}
    dirs = [n(d) for d in candidate_dirs(env=env, home=HOME, brew=None, go_bin=None)]
    assert n("/c/bin") in dirs and n("/b") in dirs


def test_it_never_touches_a_shell_profile(tmp_path):
    """Child PATH only: a profile in a fake home stays byte-identical."""
    profile = tmp_path / ".bashrc"
    profile.write_text("# mine\n", encoding="utf-8")
    refreshed_path(
        "/usr/bin",
        env={},
        home=tmp_path,
        exists=lambda d: True,
        brew=lambda _p: None,
        go_bin=lambda _p: None,
    )
    assert profile.read_text(encoding="utf-8") == "# mine\n"
    assert [p.name for p in tmp_path.iterdir()] == [".bashrc"]
