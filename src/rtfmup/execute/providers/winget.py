"""winget provider.

Package table VERIFIED 2026-11-14 with winget v1.29.380:
  * existence and version: `winget show --id <id> --exact --source winget`
  * scope: the INSTALLER MANIFESTS in microsoft/winget-pkgs. `winget show --scope
    user` is not usable for this: it exits 0 even for machine-only packages.
`user_scope=True` means the manifest has a `Scope: user` installer, so
`--scope user` works and nothing needs admin. Everything else may elevate
(high risk, D12).

Not provided (nothing verified): rust. `Rustlang.Rustup` exists but installs only
rustup, not a toolchain.
"""

from __future__ import annotations

from rtfmup.execute.providers.base import Pkg, choose, runtime_step, tool_found
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

# Preference order per runtime: default first.
TABLE: dict[str, tuple[Pkg, ...]] = {
    "python": tuple(
        Pkg(f"Python.Python.3.{minor}", ver, user_scope=True)
        for minor, ver in (
            (15, "3.15.2"),
            (14, "3.14.10"),
            (13, "3.13.17"),
            (12, "3.12.10"),
            (11, "3.11.9"),
            (10, "3.10.11"),
            (9, "3.9.13"),
            (8, "3.8.10"),
        )
    ),
    # LTS first so an unpinned install gets the supported line.
    "node": (
        Pkg("OpenJS.NodeJS.LTS", "26.13.1"),
        Pkg("OpenJS.NodeJS", "26.12.1"),
    ),
    "go": (Pkg("GoLang.Go", "1.27.4"),),
    # The DevKit variant, so native gems can build.
    "ruby": tuple(
        Pkg(f"RubyInstallerTeam.RubyWithDevKit.3.{minor}", ver, user_scope=True)
        for minor, ver in (
            (4, "3.4.11"),
            (3, "3.3.12"),
            (2, "3.2.11"),
            (1, "3.1.7"),
            (0, "3.0.7"),
        )
    ),
    "php": tuple(
        Pkg(f"PHP.PHP.8.{minor}", ver)
        for minor, ver in ((5, "8.5.12"), (4, "8.4.27"), (3, "8.3.36"), (2, "8.2.35"))
    ),
    "dotnet": tuple(
        Pkg(f"Microsoft.DotNet.SDK.{major}", ver)
        for major, ver in (
            (11, "11.0.100"),
            (10, "10.0.404"),
            (9, "9.0.321"),
            (8, "8.0.428"),
            (7, "7.0.410"),
            (6, "6.0.428"),
        )
    ),
    "bun": (Pkg("Oven-sh.Bun", "1.4.4"),),
}


class WingetProvider:
    name = "winget"

    def available(self, host: HostProfile) -> bool:
        return host.os == "windows" and tool_found(host, "winget")

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
            raise ValueError(f"winget cannot provide {runtime} {spec or ''}".strip())
        argv = [
            "winget",
            "install",
            "--id",
            pkg.name,
            "--exact",
            "--source",
            "winget",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        if pkg.user_scope:
            argv += ["--scope", "user"]
            return runtime_step(
                self.name,
                runtime,
                pkg.name,
                argv,
                risk="medium",
                reasons=["downloads and installs a package (per-user)"],
            )
        return runtime_step(
            self.name,
            runtime,
            pkg.name,
            argv,
            risk="high",
            reasons=["winget may ask for elevation (no per-user installer)"],
        )
