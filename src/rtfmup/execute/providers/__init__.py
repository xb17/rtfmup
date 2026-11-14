"""Provider selection (D12). Providers produce Steps; nothing here executes."""

from __future__ import annotations

from collections.abc import Sequence

from rtfmup.execute.providers.base import Provider
from rtfmup.execute.providers.choco import ChocoProvider
from rtfmup.execute.providers.manual import ManualProvider
from rtfmup.execute.providers.scoop import ScoopProvider
from rtfmup.execute.providers.uv_python import UvPythonProvider
from rtfmup.execute.providers.winget import WingetProvider
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

# D12: user-scope providers before system-scope ones. `mise` joins in M5.
WINDOWS_ORDER = ("uv_python", "scoop", "winget", "choco", "manual")


def all_providers() -> dict[str, Provider]:
    providers: list[Provider] = [
        UvPythonProvider(),
        ScoopProvider(),
        WingetProvider(),
        ChocoProvider(),
        ManualProvider(),
    ]
    return {p.name: p for p in providers}


def select_steps(
    runtime: str,
    spec: str | None,
    host: HostProfile,
    *,
    order: Sequence[str] = WINDOWS_ORDER,
    providers: dict[str, Provider] | None = None,
) -> list[Step]:
    """Steps that provision `runtime`, from the first provider that can.

    Prerequisites come first. `manual` is the fallback and yields a single
    non-runnable step. Unknown names in `order` are ignored.
    """
    table = providers if providers is not None else all_providers()
    for name in order:
        p = table.get(name)
        if p is None or not p.available(host) or not p.can_provide(runtime, spec):
            continue
        return [*p.prerequisite_steps(runtime, spec), p.install_step(runtime, spec)]
    return []
