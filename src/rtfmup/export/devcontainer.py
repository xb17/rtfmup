"""D20: Analysis + Plan -> devcontainer.json.

The result is plain JSON. Our commentary goes under `customizations.rtfmup`, which the
schema leaves open, so the file stays valid for every tool that reads it.

Mapping (runtime -> Feature id + major) was verified on 2026-11-08 against
https://github.com/devcontainers/features (`src/<id>/devcontainer-feature.json`
on `main`) and the base image against
https://github.com/devcontainers/images/blob/main/src/base-ubuntu/README.md.
Re-check both before changing an entry.
"""

from __future__ import annotations

import posixpath
import shlex
from pathlib import Path
from typing import Any

import yaml

from rtfmup.host.probe import HostProfile
from rtfmup.host.ranges import minimum_version
from rtfmup.models import Analysis, Component
from rtfmup.plan.gaps import RUNTIME_OF_EXE, component_runtimes
from rtfmup.plan.model import Plan, Step

# Verified 2026-11-08. Every Feature below takes its version in the `version` option.
FEATURES: dict[str, str] = {
    "node": "ghcr.io/devcontainers/features/node:2",
    "python": "ghcr.io/devcontainers/features/python:1",
    "go": "ghcr.io/devcontainers/features/go:1",
    "rust": "ghcr.io/devcontainers/features/rust:1",
    "dotnet": "ghcr.io/devcontainers/features/dotnet:2",
    "ruby": "ghcr.io/devcontainers/features/ruby:2",
    "php": "ghcr.io/devcontainers/features/php:1",
    "java": "ghcr.io/devcontainers/features/java:1",
}
# Runtimes with no official Feature: reported in the notes, never guessed.
NO_FEATURE = {"bun": "Bun", "deno": "Deno"}
BASE_IMAGE = "mcr.microsoft.com/devcontainers/base:ubuntu24.04"  # verified 2026-11-08
WORKSPACE = "/workspaces/${localWorkspaceFolderBasename}"
DEVCONTAINER_STEPS = ("install", "env", "setup", "build")


def linux_host() -> HostProfile:
    """The container is Linux whatever this machine is. No tools probed: no gaps."""
    return HostProfile(os="linux", arch="x86_64", tools={}, docker_daemon=None)


def _exe(step: Step) -> str:
    if step.argv:
        return posixpath.basename(step.argv[0]).lower()
    words = (step.shell_text or "").split()
    return posixpath.basename(words[0]).lower() if words else ""


def _in_dir(cwd: str, command: str) -> str:
    if cwd in (".", ""):
        return command
    return f"(cd {shlex.quote(cwd)} && {command})"


def _step_command(step: Step) -> str | None:
    text = shlex.join(step.argv) if step.argv else step.shell_text
    if not text:
        return None
    if step.env:
        pairs = " ".join(f"{k}={shlex.quote(v)}" for k, v in step.env.items())
        text = f"env {pairs} {text}"
    return _in_dir(step.cwd, text)


def _env_command(a: Analysis, step: Step) -> str:
    """Create .env from the template unless one already exists."""
    here = "" if step.cwd in (".", "") else step.cwd
    template = next(
        (
            posixpath.basename(m.file)
            for m in a.manifests
            if m.kind == "env_template" and posixpath.dirname(m.file) == here
        ),
        ".env.example",
    )
    # Parenthesised so `a && (test || cp) && b` cannot let a failure of `a` reach `cp`.
    copy = f"(test -f .env || cp {shlex.quote(template)} .env)"
    return copy if step.cwd in (".", "") else _in_dir(step.cwd, copy)


def _features(a: Analysis, comp: Component, plan: Plan) -> tuple[dict, list[str]]:
    wanted: dict[str, str | None] = {}
    for r in component_runtimes(a, comp):
        version = minimum_version(r.name, r.version_spec) if r.version_spec else None
        if wanted.get(r.name) is None:
            wanted[r.name] = version
    for s in plan.steps:
        if s.platform_ok and (name := RUNTIME_OF_EXE.get(_exe(s))):
            wanted.setdefault(name, None)
    features: dict[str, dict[str, str]] = {}
    notes: list[str] = []
    for name in sorted(wanted):
        if name in NO_FEATURE:
            notes.append(
                f"{NO_FEATURE[name]} has no official dev container Feature; "
                "install it yourself."
            )
        elif name in FEATURES:
            version = wanted[name]
            features[FEATURES[name]] = {"version": version} if version else {}
    return features, notes


def _compose(a: Analysis, root: Path) -> tuple[str, str] | None:
    """(compose file relative to the repo, the app service) when it is unambiguous."""
    for m in a.manifests:
        if m.kind != "compose":
            continue
        try:
            data = yaml.safe_load((root / m.file).read_text(encoding="utf-8-sig"))
        except (OSError, yaml.YAMLError):
            return None
        services = data.get("services") if isinstance(data, dict) else None
        if not isinstance(services, dict) or not services:
            return None
        built = [n for n, s in services.items() if isinstance(s, dict) and "build" in s]
        # Only a service built from the repo can be the app; a lone `image:` service
        # is a database or cache, never the dev container.
        return (m.file, str(built[0])) if len(built) == 1 else None
    return None


def build_devcontainer(
    a: Analysis, plan: Plan, *, root: Path | None = None
) -> dict[str, Any]:
    """The devcontainer.json document for `plan` (built for a Linux host)."""
    root = root or Path(a.root)
    comp = next((c for c in a.components if c.id == plan.component), None) or Component(
        id="root", path=".", runtimes=[], package_managers=[], runnable=False
    )
    notes: list[str] = []
    doc: dict[str, Any] = {"name": f"{root.name} (rtfmup)"}

    compose = _compose(a, root) if plan.variant == "docker" else None
    if plan.variant == "docker" and compose is None:
        notes.append(
            "Docker variant chosen, but no single app service could be picked from "
            "the compose file; using a plain image. Pick the service yourself."
        )
    if compose:
        file, service = compose
        doc["dockerComposeFile"] = posixpath.join(
            "..", file
        )  # the json lives in .rtfmup/
        doc["service"] = service
        doc["workspaceFolder"] = WORKSPACE
        notes.append(
            f"Compose mode: the '{service}' service must mount the repository at "
            f"{WORKSPACE} for the editor to find your code."
        )
    else:
        doc["image"] = BASE_IMAGE

    features, feature_notes = _features(a, comp, plan)
    notes += feature_notes
    if features:
        doc["features"] = features

    commands: list[str] = []
    if any(_exe(s) in ("uv", "uvx") for s in plan.steps if s.platform_ok):
        commands.append("pip install uv")  # documented: docs.astral.sh/uv installation
        notes.append(
            "uv is installed with pip because no official Feature provides it."
        )
    for s in plan.steps:
        if s.kind not in DEVCONTAINER_STEPS:
            continue
        if not s.platform_ok:
            notes.append(f"Left out (needs another OS): {s.title}")
            continue
        cmd = _env_command(a, s) if s.kind == "env" else _step_command(s)
        if cmd:
            commands.append(cmd)
            if s.risk == "high":
                notes.append(
                    f"Review before building: `{s.title}` runs at container creation "
                    f"({'; '.join(s.risk_reasons)})."
                )
    if commands:
        doc["postCreateCommand"] = " && ".join(commands)

    run = next((s for s in plan.steps if s.kind == "run"), None)
    if run is not None:
        text = shlex.join(run.argv) if run.argv else (run.shell_text or run.title)
        notes.append(f"Start the app with: {text}")

    service_ports = {p for svc in a.services for p in svc.ports}
    ports = sorted(
        {
            p.port
            for p in a.ports
            if p.port not in service_ports and p.component in (comp.id, None)
        }
    )
    if ports:
        doc["forwardPorts"] = ports

    for g in plan.gaps:
        if g.kind in (
            "missing_env",
            "external_account",
            "service",
            "unsupported_platform",
        ):
            notes.append(g.message)
    if notes:
        doc["customizations"] = {"rtfmup": {"notes": notes}}
    return doc
