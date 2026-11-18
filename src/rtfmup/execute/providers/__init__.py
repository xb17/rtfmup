"""Provider selection (D12). Providers produce Steps; nothing here executes."""

from __future__ import annotations

from collections.abc import Sequence

from rtfmup.execute.providers.apt import AptProvider
from rtfmup.execute.providers.base import Provider
from rtfmup.execute.providers.brew import BrewProvider
from rtfmup.execute.providers.choco import ChocoProvider
from rtfmup.execute.providers.dnf import DnfProvider
from rtfmup.execute.providers.manual import ManualProvider
from rtfmup.execute.providers.mise import MiseProvider
from rtfmup.execute.providers.pacman import PacmanProvider
from rtfmup.execute.providers.scoop import ScoopProvider
from rtfmup.execute.providers.uv_python import UvPythonProvider
from rtfmup.execute.providers.winget import WingetProvider
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

# D12: user-scope providers before system-scope ones, fixed per OS (a module
# constant, not configurable: Drew, 2026-11-18). Each provider checks its own distro
# in `available`, so at most one of apt/dnf/pacman is ever eligible on a Linux host.
WINDOWS_ORDER = ("uv_python", "mise", "scoop", "winget", "choco", "manual")
MACOS_ORDER = ("uv_python", "mise", "brew", "manual")
LINUX_ORDER = ("uv_python", "mise", "apt", "dnf", "pacman", "manual")
ORDER_BY_OS = {"windows": WINDOWS_ORDER, "macos": MACOS_ORDER, "linux": LINUX_ORDER}


def all_providers() -> dict[str, Provider]:
    providers: list[Provider] = [
        UvPythonProvider(),
        MiseProvider(),
        ScoopProvider(),
        WingetProvider(),
        ChocoProvider(),
        BrewProvider(),
        AptProvider(),
        DnfProvider(),
        PacmanProvider(),
        ManualProvider(),
    ]
    return {p.name: p for p in providers}


def select_steps(
    runtime: str,
    spec: str | None,
    host: HostProfile,
    *,
    order: Sequence[str] | None = None,
    providers: dict[str, Provider] | None = None,
) -> list[Step]:
    """Steps that provision `runtime`, from the first provider that can.

    Prerequisites come first. `manual` is the fallback and yields a single
    non-runnable step. Unknown names in `order` are ignored.
    """
    table = providers if providers is not None else all_providers()
    for name in order if order is not None else ORDER_BY_OS[host.os]:
        p = table.get(name)
        if p is None or not p.available(host) or not p.can_provide(runtime, spec):
            continue
        return [
            *p.prerequisite_steps(runtime, spec, host),
            p.install_step(runtime, spec, host),
        ]
    return []
