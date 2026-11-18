"""Chocolatey provider. Always HIGH risk (D12).

Package table VERIFIED 2026-11-14 with Chocolatey v2.7.5 using
`choco search <name> --exact --limit-output`. Flags checked against
`choco install --help` (`-y`, `--no-progress`).

Elevation: the `choco install` documentation page does not say whether
non-admin installs work, so we do not claim it. Every Chocolatey install is rated
high risk and asks individually. Whether it elevates is for T3.3b to record.

Not provided (not verified): rust (the `rust` package exists but what it installs
was not checked), python 3.14/3.15 pinned names, `dotnet-7.0-sdk`.
"""

from __future__ import annotations

from rtfmup.execute.providers.base import Pkg, choose, runtime_step, tool_found
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

TABLE: dict[str, tuple[Pkg, ...]] = {
    "python": (
        Pkg("python", "3.15.2"),
        Pkg("python313", "3.13.17"),
        Pkg("python312", "3.12.10"),
        Pkg("python311", "3.11.9"),
        Pkg("python310", "3.10.11"),
        Pkg("python39", "3.9.13"),
    ),
    "node": (Pkg("nodejs-lts", "26.13.1"), Pkg("nodejs", "26.13.1")),
    "go": (Pkg("golang", "1.27.4"),),
    "ruby": (Pkg("ruby", "3.4.12"),),
    "php": (Pkg("php", "8.5.12"),),
    "dotnet": (
        Pkg("dotnet-sdk", "11.0.100"),
        Pkg("dotnet-10.0-sdk", "10.0.404"),
        Pkg("dotnet-9.0-sdk", "9.0.321"),
        Pkg("dotnet-8.0-sdk", "8.0.428"),
        Pkg("dotnet-6.0-sdk", "6.0.428"),
    ),
    "bun": (Pkg("bun", "1.4.4"),),
}


class ChocoProvider:
    name = "choco"

    def available(self, host: HostProfile) -> bool:
        return host.os == "windows" and tool_found(host, "choco")

    def can_provide(self, runtime: str, spec: str | None) -> bool:
        return choose(TABLE, runtime, spec) is not None

    def prerequisite_steps(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> list[Step]:
        return []

    def install_step(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> Step:
        pkg = choose(TABLE, runtime, spec)
        if pkg is None:
            raise ValueError(f"choco cannot provide {runtime} {spec or ''}".strip())
        return runtime_step(
            self.name,
            runtime,
            pkg.name,
            ["choco", "install", pkg.name, "-y", "--no-progress"],
            risk="high",
            reasons=["Chocolatey installs are machine-wide and need administrator"],
        )
