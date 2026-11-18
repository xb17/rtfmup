"""manual provider: points at the official download page. Never runnable.

Always available and last in every order, so a missing runtime always ends in
something the user can act on. The links live in `plan/gaps.py` (checked
2026-11-07).
"""

from __future__ import annotations

from rtfmup.host.probe import HostProfile
from rtfmup.plan.gaps import MANUAL_FIX
from rtfmup.plan.model import Step


class ManualProvider:
    name = "manual"

    def available(self, host: HostProfile) -> bool:
        return True

    def can_provide(self, runtime: str, spec: str | None) -> bool:
        return runtime in MANUAL_FIX

    def prerequisite_steps(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> list[Step]:
        return []

    def install_step(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> Step:
        if runtime not in MANUAL_FIX:
            raise ValueError(f"no official download page known for {runtime}")
        return Step(
            id=f"runtime-{runtime}-manual",
            kind="manual",
            title=MANUAL_FIX[runtime],
            risk="low",
        )
