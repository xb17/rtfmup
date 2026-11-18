"""Scoop provider (user scope, medium risk).

Package table VERIFIED 2026-11-14 against the manifests in the ScoopInstaller
`Main` and `Versions` bucket repositories (what `scoop search` reads). Scoop itself
is not installed on the verification machine, so names were checked in the buckets,
not with a local `scoop` command.

Not provided: rust is `Main/rust`; no pinned older Rust lines exist, so only an
unpinned or `>=` spec resolves.

Buckets other than `main` need `scoop bucket add <name>` first. That is its own
medium-risk step, emitted only when the bucket is not already present.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from rtfmup.execute.providers.base import Pkg, choose, runtime_step, tool_found
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

V = "versions"

TABLE: dict[str, tuple[Pkg, ...]] = {
    "python": (
        Pkg("python", "3.15.2"),
        Pkg("python314", "3.14.10", bucket=V),
        Pkg("python313", "3.13.17", bucket=V),
        Pkg("python312", "3.12.10", bucket=V),
        Pkg("python311", "3.11.9", bucket=V),
        Pkg("python310", "3.10.11", bucket=V),
    ),
    "node": (
        Pkg("nodejs-lts", "26.13.1"),
        Pkg("nodejs", "26.13.1"),
        Pkg("nodejs24", "24.22.3", bucket=V),
        Pkg("nodejs22", "22.23.5", bucket=V),
        Pkg("nodejs20", "20.20.2", bucket=V),
        Pkg("nodejs18", "18.20.8", bucket=V),
    ),
    "rust": (Pkg("rust", "1.100.0"),),
    "go": (
        Pkg("go", "1.27.4"),
        Pkg("go124", "1.24.13", bucket=V),
        Pkg("go123", "1.23.12", bucket=V),
        Pkg("go122", "1.22.12", bucket=V),
    ),
    "ruby": (
        Pkg("ruby", "4.0.9"),
        Pkg("ruby34", "3.4.12", bucket=V),
        Pkg("ruby33", "3.3.13", bucket=V),
        Pkg("ruby32", "3.2.11", bucket=V),
        Pkg("ruby31", "3.1.7", bucket=V),
        Pkg("ruby30", "3.0.7", bucket=V),
    ),
    "php": (
        Pkg("php", "8.5.12"),
        Pkg("php84", "8.4.27", bucket=V),
        Pkg("php83", "8.3.36", bucket=V),
        Pkg("php82", "8.2.35", bucket=V),
    ),
    "dotnet": (
        Pkg("dotnet-sdk", "11.0.100"),
        Pkg("dotnet10-sdk", "10.0.404", bucket=V),
        Pkg("dotnet9-sdk", "9.0.321", bucket=V),
        Pkg("dotnet8-sdk", "8.0.428", bucket=V),
        Pkg("dotnet7-sdk", "7.0.410", bucket=V),
        Pkg("dotnet6-sdk", "6.0.428", bucket=V),
    ),
    "bun": (Pkg("bun", "1.4.4"),),
}


def installed_buckets() -> set[str]:
    """Names of the buckets Scoop already has (read-only directory listing).

    Scoop keeps each bucket as a folder under `<scoop root>/buckets`, and the root
    is `$SCOOP` or `~/scoop`. UNVERIFIED on a real Scoop install (none on the
    development machine); T3.3b checks it on a runner. If it under-reports, the
    `bucket add` step fails with "already exists" and nothing else is harmed.
    """
    root = Path(os.environ.get("SCOOP") or Path.home() / "scoop")
    try:
        return {p.name.lower() for p in (root / "buckets").iterdir() if p.is_dir()}
    except OSError:
        return set()


class ScoopProvider:
    name = "scoop"

    def __init__(self, buckets: Callable[[], set[str]] = installed_buckets) -> None:
        self._buckets = buckets

    def available(self, host: HostProfile) -> bool:
        return host.os == "windows" and tool_found(host, "scoop")

    def can_provide(self, runtime: str, spec: str | None) -> bool:
        return choose(TABLE, runtime, spec) is not None

    def prerequisite_steps(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> list[Step]:
        pkg = choose(TABLE, runtime, spec)
        if pkg is None or pkg.bucket == "main":
            return []
        if pkg.bucket in self._buckets():
            return []
        return [
            Step(
                id=f"scoop-bucket-{pkg.bucket}",
                kind="runtime",
                title=f"Add the Scoop '{pkg.bucket}' bucket",
                argv=["scoop", "bucket", "add", pkg.bucket],
                risk="medium",
                risk_reasons=["adds a package source to Scoop"],
            )
        ]

    def install_step(
        self, runtime: str, spec: str | None, host: HostProfile | None = None
    ) -> Step:
        pkg = choose(TABLE, runtime, spec)
        if pkg is None:
            raise ValueError(f"scoop cannot provide {runtime} {spec or ''}".strip())
        name = pkg.name if pkg.bucket == "main" else f"{pkg.bucket}/{pkg.name}"
        return runtime_step(
            self.name,
            runtime,
            pkg.name,
            ["scoop", "install", name],
            risk="medium",
            reasons=["downloads and installs a package (per-user)"],
        )
