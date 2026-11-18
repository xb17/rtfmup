"""Provider protocol and shared table helpers (PLAN §2.4, D12).

Providers only PRODUCE Steps. They never execute anything. The runner runs those
steps under the normal confirmation and risk rules.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rtfmup.host.probe import HostProfile
from rtfmup.host.ranges import satisfies
from rtfmup.plan.model import Step


@dataclass(frozen=True)
class Pkg:
    """One installable package: its exact name and the version it provides.

    `version` is the version seen when the table was verified. It is only used to
    decide which package satisfies a spec; the package manager installs whatever
    is current for that name.
    """

    name: str
    version: str
    user_scope: bool = False  # winget only: the manifest offers a per-user installer
    bucket: str = "main"  # scoop only
    also: tuple[str, ...] = ()  # apt/dnf/pacman: packages installed in the same command


class Provider(Protocol):
    name: str

    def available(self, host: HostProfile) -> bool:
        """Is this provider's tool installed? (rtfmup never installs one.)"""
        ...

    def can_provide(self, runtime: str, spec: str | None) -> bool: ...

    def install_step(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> Step:
        """The install step. Never executes. `host` matters to providers whose
        command depends on it (`sudo` only when not root)."""
        ...

    def prerequisite_steps(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> list[Step]:
        """Steps that must run first (for example adding a Scoop bucket)."""
        ...


def sudo_prefix(host: HostProfile | None) -> list[str]:
    """`["sudo"]` unless the effective uid is 0 (T5.3). An unknown uid counts as not
    root: asking for sudo is the safe side. The step is high risk either way."""
    return [] if host is not None and host.is_root else ["sudo"]


def can_elevate(host: HostProfile) -> bool:
    """Root, or a `sudo` on PATH (presence only; sudo is never run to find out)."""
    return bool(host.is_root) or tool_found(host, "sudo")


def tool_found(host: HostProfile, tool: str) -> bool:
    info = host.tools.get(tool)
    return bool(info and info.found)


def choose(
    table: dict[str, Sequence[Pkg]], runtime: str, spec: str | None
) -> Pkg | None:
    """The first package in the runtime's preference order that satisfies `spec`.

    Each table lists packages in preference order (the default first when no spec
    is given). With a spec, a package qualifies only when its version provably
    satisfies it; an unverifiable spec matches nothing, so the caller falls through
    to the next provider instead of installing something unrelated.
    """
    pkgs = table.get(runtime)
    if not pkgs:
        return None
    if not spec or not spec.strip():
        return pkgs[0]
    for pkg in pkgs:
        if satisfies(runtime, pkg.version, spec) is True:
            return pkg
    return None


def runtime_step(
    provider: str,
    runtime: str,
    pkg_name: str,
    argv: list[str],
    *,
    risk: str,
    reasons: list[str],
) -> Step:
    return Step(
        id=f"runtime-{runtime}-{provider}",
        kind="runtime",
        title=f"Install {runtime} with {provider} ({pkg_name})",
        argv=argv,
        risk=risk,  # type: ignore[arg-type]
        risk_reasons=reasons,
    )
