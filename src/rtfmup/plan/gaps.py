"""T2.4: what is missing between this host and a working setup.

Each gap says what is wrong and, where we know it, how to fix it. M2 only has the
`manual` provider: the fix is a pointer to the official download page. Real providers
(winget, scoop, ...) arrive in M3 and replace `fix`.

Nothing here executes anything.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable

from rtfmup.host.probe import HostProfile
from rtfmup.host.ranges import satisfies
from rtfmup.models import Analysis, Component, Runtime
from rtfmup.plan.model import Gap, Step

# runtime name -> the probed tool that proves it is installed
TOOL_FOR_RUNTIME = {
    "python": "python",
    "node": "node",
    "rust": "rustc",
    "go": "go",
    "ruby": "ruby",
    "php": "php",
    "dotnet": "dotnet",
    "bun": "bun",
}
# executable -> the runtime it belongs to
RUNTIME_OF_EXE = {
    "node": "node",
    "npm": "node",
    "npx": "node",
    "python": "python",
    "python3": "python",
    "py": "python",
    "pip": "python",
    "pip3": "python",
    "cargo": "rust",
    "rustc": "rust",
    "go": "go",
    "ruby": "ruby",
    "bundle": "ruby",
    "gem": "ruby",
    "php": "php",
    "dotnet": "dotnet",
    "bun": "bun",
    "bunx": "bun",
}
# executables that are their own tool
OWN_TOOL = {
    "pnpm": "pnpm",
    "yarn": "yarn",
    "uv": "uv",
    "uvx": "uv",
    "composer": "composer",
    "docker": "docker",
    "git": "git",
}

# Official landing pages only (the `manual` provider). Checked 2026-11-07.
MANUAL_FIX = {
    "python": "Install Python from https://www.python.org/downloads/",
    "node": "Install Node.js from https://nodejs.org/en/download",
    "rust": "Install Rust with rustup: https://rustup.rs",
    "go": "Install Go from https://go.dev/dl/",
    "ruby": "Install Ruby from https://www.ruby-lang.org/en/downloads/",
    "php": "Install PHP from https://www.php.net/downloads",
    "dotnet": "Install the .NET SDK from https://dotnet.microsoft.com/download",
    "bun": "Install Bun from https://bun.sh",
    "pnpm": "Install pnpm: https://pnpm.io/installation",
    "yarn": "Install Yarn: https://yarnpkg.com/getting-started/install",
    "uv": "Install uv: https://docs.astral.sh/uv/getting-started/installation/",
    "composer": "Install Composer: https://getcomposer.org/download/",
    "docker": "Install Docker: https://docs.docker.com/get-started/get-docker/",
    "git": "Install Git: https://git-scm.com/downloads",
}
DISPLAY = {
    "python": "Python",
    "node": "Node.js",
    "rust": "Rust",
    "go": "Go",
    "ruby": "Ruby",
    "php": "PHP",
    "dotnet": ".NET SDK",
    "bun": "Bun",
}

_CHANNEL = re.compile(r"^(?:stable|beta|nightly)(?:-.*)?$", re.IGNORECASE)
_KIND_ORDER = (
    "missing_runtime",
    "runtime_version",
    "missing_env",
    "external_account",
    "service",
    "unsupported_platform",
    "unresolved",
)


def owner_of(components: list[Component], directory: str) -> Component | None:
    """The deepest component whose directory contains `directory`."""
    best: Component | None = None
    for c in components:
        inside = (
            c.path == "." or directory == c.path or directory.startswith(c.path + "/")
        )
        if inside and (best is None or len(c.path) > len(best.path)):
            best = c
    return best


def _where(r: Runtime) -> str:
    ev = r.evidence[0]
    return f"{ev.file}:{ev.line_start}" if ev.line_start else ev.file


def component_runtimes(a: Analysis, comp: Component) -> list[Runtime]:
    out: list[Runtime] = []
    for r in a.runtimes:
        if r.name not in comp.runtimes:
            continue
        owner = owner_of(a.components, posixpath.dirname(r.evidence[0].file) or ".")
        if owner is not None and owner.id == comp.id:
            out.append(r)
    return out


def _exe(step: Step) -> str | None:
    """The program a step starts, from argv or the first word of its shell text."""
    if step.argv:
        name = step.argv[0]
    elif step.shell_text:
        words = step.shell_text.split()
        if not words:
            return None
        name = words[0]
    else:
        return None
    name = posixpath.basename(name.replace("\\", "/")).lower()
    return re.sub(r"\.(?:exe|cmd|bat|ps1)$", "", name)


def compute_gaps(
    a: Analysis,
    comp: Component,
    variant: str | None,
    steps: list[Step],
    host: HostProfile,
    carried: Iterable[Gap] = (),
    provision: Callable[[str, str | None], list[Step]] | None = None,
    provisioned: list[Step] | None = None,
) -> list[Gap]:
    """Gaps for this host. With `provision`, a missing or too-old runtime that a
    provider can install becomes install steps (appended to `provisioned`) instead
    of a gap; a runtime nothing can install stays a gap with the manual fix."""
    gaps: list[Gap] = []
    provided: set[str] = set()

    def provide(runtime: str, spec: str | None) -> bool:
        if runtime in provided:
            return True
        steps_ = provision(runtime, spec) if provision else []
        if not steps_:
            return False
        provided.add(runtime)
        if provisioned is not None:
            provisioned.extend(steps_)
        return True

    uses_uv = "uv" in comp.package_managers and host.tools.get("uv") is not None
    uv_installed = uses_uv and host.tools["uv"].found

    # --- runtimes the steps start and the specs the manifests ask for ---------
    needed: dict[str, list[Runtime]] = {}
    for r in component_runtimes(a, comp):
        needed.setdefault(r.name, []).append(r)
    for s in steps:
        if not s.platform_ok:
            continue
        runtime = RUNTIME_OF_EXE.get(_exe(s) or "")
        if runtime:
            needed.setdefault(runtime, [])

    tools_missing: set[str] = set()
    for name in sorted(needed):
        tool = TOOL_FOR_RUNTIME.get(name)
        info = host.tools.get(tool) if tool else None
        if info is None:
            continue  # not probed on this OS (or never): we cannot say
        if name == "python" and uv_installed:
            continue  # `uv sync` fetches a matching Python itself
        specs = [r for r in needed[name] if r.version_spec]
        if not info.found:
            tools_missing.add(name)
            if provide(name, specs[0].version_spec if specs else None):
                continue
            want = f" {specs[0].version_spec}" if specs else ""
            where = f" ({_where(specs[0])})" if specs else ""
            gaps.append(
                Gap(
                    kind="missing_runtime",
                    message=f"{DISPLAY[name]}{want} is required{where} but is not installed.",
                    fix=MANUAL_FIX[name],
                )
            )
            continue
        seen: set[str] = set()
        for r in specs:
            spec = r.version_spec or ""
            if spec in seen or (name == "rust" and _CHANNEL.match(spec)):
                continue
            seen.add(spec)
            results = [satisfies(name, v, spec) for v in info.versions]
            if any(x is True for x in results):
                continue
            installed = ", ".join(info.versions) or "unknown version"
            if results and all(x is None for x in results) or not results:
                gaps.append(
                    Gap(
                        kind="runtime_version",
                        message=(
                            f"{DISPLAY[name]} {spec} ({_where(r)}): cannot verify "
                            f"against the installed {installed}."
                        ),
                    )
                )
            elif provide(name, spec):
                continue
            else:
                gaps.append(
                    Gap(
                        kind="runtime_version",
                        message=(
                            f"{DISPLAY[name]} {spec} is required ({_where(r)}) but "
                            f"only {installed} is installed."
                        ),
                        fix=MANUAL_FIX[name],
                    )
                )

    # --- package managers and other tools the steps start --------------------
    for s in steps:
        if not s.platform_ok:
            continue
        exe = _exe(s)
        tool = OWN_TOOL.get(exe or "")
        if tool is None or tool in tools_missing:
            continue
        info = host.tools.get(tool)
        if info is not None and not info.found:
            tools_missing.add(tool)
            gaps.append(
                Gap(
                    kind="missing_runtime",
                    message=f"`{s.title}` needs {tool}, which is not installed.",
                    fix=MANUAL_FIX[tool],
                )
            )

    # --- docker: a compose run needs the daemon, not just the client ---------
    if (
        "docker" in {_exe(s) for s in steps if s.platform_ok}
        and host.docker_daemon is False
    ):
        gaps.append(
            Gap(
                kind="service",
                message="Docker is installed but its daemon is not reachable.",
                fix="Start Docker Desktop (or the Docker service) and retry.",
            )
        )

    # --- configuration the user must provide ---------------------------------
    required = sorted(e.name for e in a.env if e.required == "required")
    if required:
        gaps.append(
            Gap(
                kind="missing_env",
                message=(
                    "Needs values for: "
                    + ", ".join(required)
                    + ". Secret-like values are asked for at run time and never stored."
                ),
                fix="Fill them in when rtfmup creates .env.",
            )
        )
    for acct in a.external_accounts:
        env = f" (for {', '.join(acct.related_env)})" if acct.related_env else ""
        gaps.append(
            Gap(
                kind="external_account",
                message=f"{acct.name}: an account or API key is needed{env}.",
                fix=acct.url,
            )
        )

    # --- services the native path expects ------------------------------------
    if variant != "docker":
        for svc in a.services:
            if svc.source != "compose":
                continue
            ports = f" on port {', '.join(map(str, svc.ports))}" if svc.ports else ""
            gaps.append(
                Gap(
                    kind="service",
                    message=(
                        f"{svc.name}{ports} is expected; the compose file defines it "
                        "but the native path does not start it."
                    ),
                    fix=f"docker compose up -d {svc.name}, or install {svc.name} yourself.",
                )
            )

    # --- steps for another OS -------------------------------------------------
    for s in steps:
        if not s.platform_ok:
            ev = s.evidence[0] if s.evidence else None
            where = f" ({ev.file}:{ev.line_start})" if ev and ev.line_start else ""
            gaps.append(
                Gap(
                    kind="unsupported_platform",
                    message=f"`{s.title}` needs a different OS than {host.os}{where}.",
                )
            )

    all_gaps = [*gaps, *carried]
    order = {k: i for i, k in enumerate(_KIND_ORDER)}
    return sorted(all_gaps, key=lambda g: order[g.kind])  # stable within a kind
