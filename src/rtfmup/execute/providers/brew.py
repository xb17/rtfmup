"""Homebrew provider (macOS; medium risk: no sudo).

Package table VERIFIED 2026-11-18 against formulae.brew.sh's JSON API
(`https://formulae.brew.sh/api/formula/<name>.json`: `versions.stable`, `keg_only`,
`deprecated`, `disabled`). Homebrew itself is not installed on the development
machine (Windows), so names were checked in the index, not with a local `brew`.

Only formulae that are NOT keg-only are listed. A keg-only formula is installed but
not linked into `<prefix>/bin`, so a later step would not find it. That rules out
every versioned formula that exists today (node@24, node@22, ruby@3.4, php@8.4,
dotnet@10 ... all `keg_only: true`), and python@3.x is left out because the
unversioned `python3` link is not guaranteed for a pinned minor. Specs those would
have served fall through to mise or the manual fix.

Not provided: python (see above; `uv_python` and `mise` come first anyway) and
everything versioned. `dotnet` is the formula, not the `dotnet-sdk` cask: a cask
installs a .pkg that needs an administrator.

Linuxbrew is out of scope (PLAN section 8), so this provider is macOS only.
"""

from __future__ import annotations

from rtfmup.execute.providers.base import Pkg, choose, runtime_step, tool_found
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

TABLE: dict[str, tuple[Pkg, ...]] = {
    "node": (Pkg("node", "26.13.1"),),
    "go": (Pkg("go", "1.27.4"),),
    "rust": (Pkg("rust", "1.100.0"),),
    "ruby": (Pkg("ruby", "4.0.9"),),
    "php": (Pkg("php", "8.5.12"),),
    "dotnet": (Pkg("dotnet", "11.0.100"),),
    "bun": (Pkg("bun", "1.4.4"),),
}


class BrewProvider:
    name = "brew"

    def available(self, host: HostProfile) -> bool:
        return host.os == "macos" and tool_found(host, "brew")

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
            raise ValueError(f"brew cannot provide {runtime} {spec or ''}".strip())
        return runtime_step(
            self.name,
            runtime,
            pkg.name,
            ["brew", "install", pkg.name],
            risk="medium",
            reasons=["downloads and installs a Homebrew formula (no sudo)"],
        )
