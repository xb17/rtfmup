"""dnf provider (Fedora and relatives; HIGH risk: needs root).

Package table VERIFIED 2026-11-18 against mdapi.fedoraproject.org (the data behind
packages.fedoraproject.org) for Fedora 43 and 44. Each `version` is the LOWER of
the two, so a satisfied spec holds on both:

    runtime  package(s)       f43        f44
    python   python3          3.14.10    3.14.10
    go       golang           1.26.11    1.26.11
    rust     rust + cargo     1.100.0    1.100.0
    ruby     ruby             3.4.12     4.0.9
    php      php-cli          8.4.27     8.5.12
    dotnet   dotnet-sdk-10.0  10.0.115   10.0.115

Not provided: node (`nodejs` exists on f43 only; f44 has `nodejs22` and `nodejs24`,
and it was not verified that those provide a plain `node` and `npm`), and bun (no
package). `-y` is documented for both dnf4 and dnf5.
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
    "python": (Pkg("python3", "3.14.10"),),
    "go": (Pkg("golang", "1.26.11"),),
    "rust": (Pkg("rust", "1.100.0", also=("cargo",)),),
    "ruby": (Pkg("ruby", "3.4.12"),),
    "php": (Pkg("php-cli", "8.4.27"),),
    "dotnet": (Pkg("dotnet-sdk-10.0", "10.0.115"),),
}

DISTROS = frozenset({"fedora", "rhel", "centos"})


class DnfProvider:
    name = "dnf"

    def available(self, host: HostProfile) -> bool:
        ids = {host.distro_id or "", *host.distro_like}
        return (
            host.os == "linux"
            and bool(ids & DISTROS)
            and tool_found(host, "dnf")
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
            raise ValueError(f"dnf cannot provide {runtime} {spec or ''}".strip())
        return runtime_step(
            self.name,
            runtime,
            " ".join((pkg.name, *pkg.also)),
            [*sudo_prefix(host), "dnf", "install", "-y", pkg.name, *pkg.also],
            risk="high",
            reasons=["installs system packages as root (sudo)"],
        )
