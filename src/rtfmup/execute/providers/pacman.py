"""pacman provider (Arch and relatives; HIGH risk: needs root).

Package table VERIFIED 2026-11-18 against archlinux.org/packages (its JSON search,
`/packages/search/json/?name=<pkg>`). Arch is rolling: the versions are what the
repositories held that day and only decide which spec a package can satisfy.

    runtime  package(s)       version
    python   python           3.14.10   (core)
    node     nodejs + npm     26.13.1   (extra; npm is its own package)
    go       go               1.27.4
    rust     rust             1.100.0   (includes cargo)
    ruby     ruby             3.4.12
    php      php              8.5.12
    dotnet   dotnet-sdk-10.0  10.0.114   (pkgver 10.0.14.sdk114)
    bun      bun              1.4.4

`pacman -S --needed --noconfirm`: documented in pacman(8). No `-y`/`-u`: a partial
upgrade is not supported on Arch, and refreshing databases without upgrading leaves the
system in exactly that state. If the install fails because the local database is stale,
the step's own error says so.
"""

from __future__ import annotations

from rtfmup.execute.providers.base import (
    Pkg,
    can_elevate,
    choose,
    runtime_step,
    sudo_prefix,
    tool_found,
)
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

TABLE: dict[str, tuple[Pkg, ...]] = {
    "python": (Pkg("python", "3.14.10"),),
    "node": (Pkg("nodejs", "26.13.1", also=("npm",)),),
    "go": (Pkg("go", "1.27.4"),),
    "rust": (Pkg("rust", "1.100.0"),),
    "ruby": (Pkg("ruby", "3.4.12"),),
    "php": (Pkg("php", "8.5.12"),),
    "dotnet": (Pkg("dotnet-sdk-10.0", "10.0.114"),),
    "bun": (Pkg("bun", "1.4.4"),),
}

DISTROS = frozenset({"arch"})


class PacmanProvider:
    name = "pacman"

    def available(self, host: HostProfile) -> bool:
        ids = {host.distro_id or "", *host.distro_like}
        return (
            host.os == "linux"
            and bool(ids & DISTROS)
            and tool_found(host, "pacman")
            and can_elevate(host)
        )

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
            raise ValueError(f"pacman cannot provide {runtime} {spec or ''}".strip())
        return runtime_step(
            self.name,
            runtime,
            " ".join((pkg.name, *pkg.also)),
            [
                *sudo_prefix(host),
                "pacman",
                "-S",
                "--needed",
                "--noconfirm",
                pkg.name,
                *pkg.also,
            ],
            risk="high",
            reasons=["installs system packages as root (sudo)"],
        )
