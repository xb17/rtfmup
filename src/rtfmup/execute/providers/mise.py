"""mise provider (all three OSes; user scope, medium risk).

Tool names VERIFIED 2026-11-18 against mise.jdx.dev/core-tools.html: `node`, `python`,
`go` and `bun` are core tools (no plugin to fetch). Not provided: rust (installs
through rustup into ~/.cargo and ~/.rustup, outside mise's own directory), dotnet
(its version syntax was not verified), ruby and php (not core tools; they build from
source or need a plugin).

The command is `mise use --global <tool>@<request>` (mise.jdx.dev/cli/use.html). It
installs the tool and records it in the global config (`~/.config/mise/config.toml`),
which is what makes a shim in `~/.local/share/mise/shims` resolve outside a project:
a bare `mise install` leaves shims failing with "No version is set". That config write
is stated in the step's risk reasons. mise accepts no ranges (`^24`, `>=20`), only
exact versions, prefixes (`24`, `3.14`), `latest` and aliases (`lts`)
(mise.jdx.dev/dev-tools/versions.html), so each table row is one release line and the
request is its prefix. `version` is the newest release in that line on 2026-11-18
(nodejs.org/dist/index.json, go.dev/dl, formulae.brew.sh for python, bun).

Redirecting where mise writes, for CI (T5.3b): MISE_DATA_DIR, MISE_CACHE_DIR,
MISE_CONFIG_DIR, MISE_STATE_DIR (mise.jdx.dev/configuration/environment-variables.html).
"""

from __future__ import annotations

from rtfmup.execute.providers.base import Pkg, choose, runtime_step, tool_found
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

# `name` is `<tool>@<request>`, newest line first.
TABLE: dict[str, tuple[Pkg, ...]] = {
    "node": (
        Pkg("node@lts", "26.13.1"),  # 26 is the current LTS line (Lithium)
        Pkg("node@24", "24.22.3"),
        Pkg("node@22", "22.23.5"),
        Pkg("node@20", "20.20.2"),
        Pkg("node@18", "18.20.8"),
    ),
    "python": (
        Pkg("python@3.15", "3.15.2"),
        Pkg("python@3.14", "3.14.10"),
        Pkg("python@3.13", "3.13.18"),
        Pkg("python@3.12", "3.12.16"),
        Pkg("python@3.11", "3.11.18"),
        Pkg("python@3.10", "3.10.22"),
        Pkg("python@3.9", "3.9.25"),
    ),
    "go": (
        Pkg("go@1.27", "1.27.4"),
        Pkg("go@1.26", "1.26.11"),
        Pkg("go@1.25", "1.25.14"),
        Pkg("go@1.24", "1.24.13"),
    ),
    "bun": (Pkg("bun@latest", "1.4.4"),),
}


class MiseProvider:
    name = "mise"

    def available(self, host: HostProfile) -> bool:
        return tool_found(host, "mise")

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
            raise ValueError(f"mise cannot provide {runtime} {spec or ''}".strip())
        return runtime_step(
            self.name,
            runtime,
            pkg.name,
            ["mise", "use", "--global", pkg.name],
            risk="medium",
            reasons=[
                "downloads and installs a tool into mise's per-user directory",
                "records it in mise's global config (~/.config/mise/config.toml)",
            ],
        )
