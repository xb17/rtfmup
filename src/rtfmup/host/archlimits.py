"""Runtime releases that do not exist for this CPU (T5.3).

A pinned old version (`node 14`) has no build for Apple silicon, and installing it
would fail halfway. The plan raises a `runtime_version` gap that says so instead.

Facts, VERIFIED 2026-11-18:
  * Node: the first release with an `osx-arm64-tar` file in nodejs.org/dist/index.json
    is v16.0.0 (280 releases have it). Linux arm64 builds go back to v4.0.0, so no
    entry for Linux.
  * Go: darwin/arm64 archives start at go1.16beta1 (go.dev/dl?mode=json&include=all),
    but a `go` directive in go.mod is a MINIMUM, so a newer toolchain always serves an
    old pin; no entry.
`old` are releases below the floor and `new` are releases at or above it, each one the
last of a line; a spec is "pinned below the floor" when it admits an old one and no
new one.
"""

from __future__ import annotations

from dataclasses import dataclass

from rtfmup.host.probe import HostProfile
from rtfmup.host.ranges import satisfies


@dataclass(frozen=True)
class Floor:
    first: str
    old: tuple[str, ...]
    new: tuple[str, ...]


FLOORS: dict[tuple[str, str, str], Floor] = {
    ("node", "macos", "arm64"): Floor(
        "16.0.0",
        ("10.24.1", "12.22.12", "14.21.3"),
        ("16.20.2", "18.20.8", "20.20.2", "22.23.5", "24.22.3", "26.13.1"),
    ),
}


def no_build_for_arch(runtime: str, spec: str | None, host: HostProfile) -> str | None:
    """What is wrong with `spec` on this CPU, or None when it is fine or unknown."""
    floor = FLOORS.get((runtime, host.os, host.arch))
    if floor is None or not spec or not spec.strip():
        return None
    wants_old = any(satisfies(runtime, v, spec) is True for v in floor.old)
    wants_new = any(satisfies(runtime, v, spec) is True for v in floor.new)
    if wants_old and not wants_new:
        return (
            f"has no {host.os} {host.arch} build; the first release for it is "
            f"{floor.first}"
        )
    return None
