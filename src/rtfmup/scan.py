"""Pass 1 (minimal): deterministic manifest detection at the repo root."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from rtfmup.detect.components import discover
from rtfmup.models import (
    Ambiguity,
    Command,
    Component,
    Deployment,
    EnvVar,
    ManifestFinding,
    PackageManager,
    Port,
    Runtime,
    Service,
)
from rtfmup.readme.locate import locate

# filename -> (category, label). Order is the display order.
MANIFESTS: dict[str, tuple[str, str]] = {
    "pyproject.toml": ("language", "Python"),
    "requirements.txt": ("language", "Python"),
    "package.json": ("language", "Node.js"),
    "Cargo.toml": ("language", "Rust"),
    "go.mod": ("language", "Go"),
    "Dockerfile": ("container", "Docker"),
    "docker-compose.yml": ("container", "Docker Compose"),
    "compose.yml": ("container", "Docker Compose"),
    ".env.example": ("config", "env template"),
}


@dataclass
class Finding:
    file: str
    category: str
    label: str


@dataclass
class ScanResult:
    root: str
    findings: list[Finding] = field(default_factory=list)
    runtimes: list[Runtime] = field(default_factory=list)
    package_managers: list[PackageManager] = field(default_factory=list)
    manifests: list[ManifestFinding] = field(default_factory=list)
    env: list[EnvVar] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)
    commands: list[Command] = field(default_factory=list)
    deployments: list[Deployment] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)
    readme: str | None = None
    readme_format: str | None = None
    has_docker: bool = False
    has_devcontainer: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def languages(self) -> list[str]:
        seen: list[str] = []
        for f in self.findings:
            if f.category == "language" and f.label not in seen:
                seen.append(f.label)
        return seen

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "languages": self.languages,
            "findings": [asdict(f) for f in self.findings],
            "runtimes": [r.model_dump(mode="json") for r in self.runtimes],
            "package_managers": [
                p.model_dump(mode="json") for p in self.package_managers
            ],
            "manifests": [m.model_dump(mode="json") for m in self.manifests],
            "env": [e.model_dump(mode="json") for e in self.env],
            "services": [x.model_dump(mode="json") for x in self.services],
            "ports": [x.model_dump(mode="json") for x in self.ports],
            "commands": [c.model_dump(mode="json") for c in self.commands],
            "deployments": [x.model_dump(mode="json") for x in self.deployments],
            "components": [c.model_dump(mode="json") for c in self.components],
            "ambiguities": [a.model_dump(mode="json") for a in self.ambiguities],
            "readme": self.readme,
            "readme_format": self.readme_format,
            "has_docker": self.has_docker,
            "has_devcontainer": self.has_devcontainer,
            "warnings": self.warnings,
        }


def scan(path: str | Path) -> ScanResult:
    root = Path(path).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    result = ScanResult(root=str(root))
    for name, (category, label) in MANIFESTS.items():
        if (root / name).is_file():
            result.findings.append(Finding(file=name, category=category, label=label))
    discovery = discover(root)
    detection = discovery.merged(root)
    result.components = discovery.components
    result.ambiguities = discovery.ambiguities
    result.runtimes = detection.runtimes
    result.package_managers = detection.package_managers
    result.manifests = detection.manifests
    result.env = detection.env
    result.services = detection.services
    result.ports = detection.ports
    result.commands = detection.commands
    result.deployments = detection.deployments
    result.has_docker = detection.has_docker
    result.has_devcontainer = detection.has_devcontainer
    located = locate(root)
    if located.readme:
        result.readme = located.readme.rel
        result.readme_format = located.readme.format
    result.warnings = sorted(detection.warnings + located.warnings)
    return result


def format_summary(result: ScanResult) -> str:
    lines = [f"Scanned: {result.root}"]
    if not result.findings:
        lines.append("No known manifests found.")
        return "\n".join(lines)
    langs = ", ".join(result.languages) or "none detected"
    lines.append(f"Languages: {langs}")
    for f in result.findings:
        lines.append(f"  {f.file:<20} {f.category:<10} {f.label}")
    return "\n".join(lines)
