"""apt provider (Debian and Ubuntu; HIGH risk: needs root).

Package table VERIFIED 2026-11-18 on packages.ubuntu.com for Ubuntu 24.04 LTS
(noble) and 26.04 LTS (resolute), the two LTS releases in standard support
(changelogs.ubuntu.com/meta-release-lts). Each `version` is the LOWER of the two, so a
spec the table says is satisfied is satisfied on both:

    runtime  package         noble             resolute
    python   python3         3.12.3            3.14.3
    node     nodejs + npm    18.19.1           22.22.1     (universe)
    go       golang-go       1.22              1.26
    rust     cargo           1.75.0            1.93.1      (pulls rustc)
    ruby     ruby            3.2               3.3
    php      php-cli         8.3               8.5
    dotnet   dotnet-sdk-10.0 10.0.114          10.0.114

Not provided: bun (no package); `dotnet-sdk-8.0` (noble only) and `-9.0` (neither
release lists it). Debian proper is not verified: its package names match, its
versions differ, and the lower-bound rule above does not cover it. The same
`apt-cache policy` check runs for these names in the Gitea `linux` job (T5.3).

Commands: `apt-get`, not `apt`, because apt(8) says the `apt` front end is for
interactive use and its output is not stable for scripts. `apt-get update` first, since
a fresh container has no package lists. `-y` and `--no-install-recommends` are
documented in apt-get(8); `DEBIAN_FRONTEND=noninteractive` (through `env`, because sudo
resets the environment) keeps debconf from prompting.
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
    "python": (Pkg("python3", "3.12.3"),),
    "node": (Pkg("nodejs", "18.19.1", also=("npm",)),),
    "go": (Pkg("golang-go", "1.22"),),
    "rust": (Pkg("cargo", "1.75.0"),),
    "ruby": (Pkg("ruby", "3.2"),),
    "php": (Pkg("php-cli", "8.3"),),
    "dotnet": (Pkg("dotnet-sdk-10.0", "10.0.114"),),
}

DISTROS = frozenset({"debian", "ubuntu"})


def is_debian_family(host: HostProfile) -> bool:
    ids = {host.distro_id or "", *host.distro_like}
    return bool(ids & DISTROS)


class AptProvider:
    name = "apt"

    def available(self, host: HostProfile) -> bool:
        return (
            host.os == "linux"
            and is_debian_family(host)
            and tool_found(host, "apt")
            and can_elevate(host)
        )

    def can_provide(self, runtime: str, spec: str | None) -> bool:
        return choose(TABLE, runtime, spec) is not None

    def prerequisite_steps(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> list[Step]:
        return [
            Step(
                id="apt-update",
                kind="runtime",
                title="Refresh the apt package lists",
                argv=[*sudo_prefix(host), "apt-get", "update"],
                risk="high",
                risk_reasons=["runs apt-get as root (changes system state)"],
            )
        ]

    def install_step(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> Step:
        pkg = choose(TABLE, runtime, spec)
        if pkg is None:
            raise ValueError(f"apt cannot provide {runtime} {spec or ''}".strip())
        argv = [
            *sudo_prefix(host),
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            pkg.name,
            *pkg.also,
        ]
        return runtime_step(
            self.name,
            runtime,
            " ".join((pkg.name, *pkg.also)),
            argv,
            risk="high",
            reasons=["installs system packages as root (sudo)"],
        )
