"""Shared state and helpers for the Pass 1 detectors.

Detectors only read files. They never execute anything from the repo (PLAN §7.8).
Unreadable or malformed files become `warnings` entries, never exceptions.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from rtfmup.models import (
    ManifestFinding,
    PackageManager,
    Runtime,
    SourceRef,
)


def find_key_line(text: str, *needles: str) -> int | None:
    """1-based line of the last needle, searching each one after the previous hit.

    `find_key_line(text, '"engines"', '"node"')` finds `"node"` inside `engines`.
    If a later needle is missing, the line of the last one found is returned.
    """
    lines = text.splitlines()
    start = 0
    found: int | None = None
    for needle in needles:
        for i in range(start, len(lines)):
            if needle in lines[i]:
                found = i + 1
                start = i + 1
                break
        else:
            break
    return found


class Detection:
    """Accumulates findings for one directory (a component, see T1.6)."""

    def __init__(self, root: Path, rel: str = "", component: str = "root") -> None:
        self.root = root
        self.rel = rel  # "" or "apps/web/" (forward slashes, trailing slash)
        self.component = component
        self.runtimes: list[Runtime] = []
        self.package_managers: list[PackageManager] = []
        self.manifests: list[ManifestFinding] = []
        self.warnings: list[str] = []

    # --- file access -------------------------------------------------------
    def relname(self, name: str) -> str:
        return f"{self.rel}{name}"

    def exists(self, name: str) -> bool:
        return (self.root / name).is_file()

    def read(self, name: str) -> str | None:
        if not self.exists(name):
            return None
        try:
            return (self.root / name).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            self.warnings.append(f"{self.relname(name)}: cannot read ({exc})")
            return None

    def json(self, name: str, text: str) -> dict[str, Any] | None:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            self.warnings.append(f"{self.relname(name)}: invalid JSON ({exc})")
            return None
        if not isinstance(data, dict):
            self.warnings.append(f"{self.relname(name)}: expected a JSON object")
            return None
        return data

    def toml(self, name: str, text: str) -> dict[str, Any] | None:
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            self.warnings.append(f"{self.relname(name)}: invalid TOML ({exc})")
            return None

    # --- recording ---------------------------------------------------------
    def ref(self, name: str, line: int | None, detector: str) -> SourceRef:
        return SourceRef(
            file=self.relname(name),
            line_start=line,
            line_end=line,
            detector=detector,
        )

    def runtime(
        self, runtime: str, spec: str | None, name: str, line: int | None, detector: str
    ) -> None:
        self.runtimes.append(
            Runtime(
                name=runtime,  # type: ignore[arg-type]
                version_spec=spec,
                confidence="high",
                evidence=[self.ref(name, line, detector)],
            )
        )

    def package_manager(
        self,
        pm: str,
        lockfile: str | None,
        evidence_file: str,
        line: int | None,
        detector: str,
    ) -> None:
        ref = self.ref(evidence_file, line, detector)
        for existing in self.package_managers:
            if existing.name == pm:
                if existing.lockfile is None and lockfile is not None:
                    existing.lockfile = self.relname(lockfile)
                existing.evidence.append(ref)
                return
        self.package_managers.append(
            PackageManager(
                name=pm,
                lockfile=self.relname(lockfile) if lockfile else None,
                component=self.component,
                evidence=[ref],
            )
        )

    def manifest(
        self, name: str, kind: str, detector: str, line: int | None = 1
    ) -> None:
        self.manifests.append(
            ManifestFinding(
                file=self.relname(name),
                kind=kind,
                component=self.component,
                evidence=[self.ref(name, line, detector)],
            )
        )

    def sort(self) -> None:
        """Deterministic order: evidence file, line, then name (PLAN §2.2)."""

        def key(ev: list[SourceRef], name: str) -> tuple[str, int, str]:
            e = ev[0]
            return (e.file, e.line_start or 0, name)

        self.runtimes.sort(key=lambda r: key(r.evidence, r.name))
        self.package_managers.sort(key=lambda p: key(p.evidence, p.name))
        self.manifests.sort(key=lambda m: key(m.evidence, m.kind))
        self.warnings.sort()
