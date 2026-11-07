"""Component discovery (D14): the root, declared workspace members, and the
conventional one-level directories. Read-only."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from rtfmup.detect import detect_dir
from rtfmup.detect._common import Detection
from rtfmup.models import Ambiguity, Component

CONVENTIONAL = ("server", "client", "backend", "frontend", "api", "web", "app")
CONVENTIONAL_GLOBS = ("apps/*", "packages/*", "services/*")
MAX_COMPONENTS = 100
RUN_SCRIPTS = ("dev", "start", "serve")
RUN_FILES = ("main.py", "app.py", "manage.py", "main.go", "artisan", "bin/rails")
_GLOB_CHARS = re.compile(r"[*?\[]")


@dataclass
class Discovery:
    components: list[Component]
    detections: dict[str, Detection]  # component id -> its detection
    ambiguities: list[Ambiguity] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merged(self, root: Path) -> Detection:
        """All per-component findings in one sorted Detection (command ids renumbered)."""
        agg = Detection(root)
        for det in self.detections.values():
            agg.runtimes += det.runtimes
            agg.package_managers += det.package_managers
            agg.manifests += det.manifests
            agg.env += det.env
            agg.services += det.services
            agg.ports += det.ports
            agg.commands += det.commands
            agg.deployments += det.deployments
            agg.warnings += det.warnings
            agg.has_docker |= det.has_docker
            agg.has_devcontainer |= det.has_devcontainer
        agg.warnings += self.warnings
        agg.sort()
        return agg


# --- workspace declarations ---------------------------------------------------


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}  # the manifest detectors already warned about it


def _strings(value: object) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def workspace_patterns(root: Path, warnings: list[str]) -> list[str]:
    """Member globs/paths declared by npm/yarn/pnpm, Cargo, uv and Go workspaces."""
    out: list[str] = []

    pkg = root / "package.json"
    if pkg.is_file():
        import json

        try:
            data = json.loads(pkg.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        ws = data.get("workspaces") if isinstance(data, dict) else None
        if isinstance(ws, dict):
            ws = ws.get("packages")
        out += _strings(ws)

    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        try:
            data = yaml.safe_load(pnpm.read_text(encoding="utf-8-sig"))
            out += _strings(data.get("packages") if isinstance(data, dict) else None)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            warnings.append(
                f"pnpm-workspace.yaml: cannot parse ({str(exc).splitlines()[0]})"
            )

    cargo = _toml(root / "Cargo.toml")
    ws = cargo.get("workspace")
    if isinstance(ws, dict):
        out += _strings(ws.get("members"))

    uv = _toml(root / "pyproject.toml").get("tool", {})
    uv_ws = uv.get("uv", {}).get("workspace") if isinstance(uv, dict) else None
    if isinstance(uv_ws, dict):
        out += _strings(uv_ws.get("members"))

    gowork = root / "go.work"
    if gowork.is_file():
        try:
            out += parse_go_work(gowork.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            pass
    return out


def parse_go_work(text: str) -> list[str]:
    """`use ./a` and `use ( ./a ./b )` forms."""
    out: list[str] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.split("//", 1)[0].strip()
        if in_block:
            if line == ")":
                in_block = False
            elif line:
                out.append(line.strip('"'))
        elif line.startswith("use"):
            rest = line[3:].strip()
            if rest == "(":
                in_block = True
            elif rest:
                out.append(rest.strip('"'))
    return out


# --- expansion ----------------------------------------------------------------


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def expand(root: Path, pattern: str) -> list[Path]:
    """Directories matching a workspace pattern. Never leaves the repo."""
    if pattern.startswith(("!", "/")) or ".." in Path(pattern).parts:
        return []
    pattern = pattern.removeprefix("./").rstrip("/")
    if not pattern or pattern == ".":
        return []
    if _GLOB_CHARS.search(pattern):
        candidates = sorted(root.glob(pattern))
    else:
        candidates = [root / pattern]
    return [
        p
        for p in candidates
        if p.is_dir()
        and not p.is_symlink()
        and not any(
            part.startswith(".") or part == "node_modules"
            for part in p.relative_to(root).parts
        )
        and _inside(root, p)
    ]


def _runnable(path: Path, det: Detection) -> bool:
    if any(s in det.scripts for s in RUN_SCRIPTS):
        return True
    if any(
        k.split(":", 1)[-1] in ("run", "dev", "start", "serve")
        for k in det.scripts
        if ":" in k
    ):
        return True
    if any(c.category == "run" for c in det.commands):  # Procfile web:
        return True
    if (
        any(m.kind == "cargo_toml" for m in det.manifests)
        and (path / "src" / "main.rs").is_file()
    ):
        return True
    if any((path / f).is_file() for f in RUN_FILES):
        return True
    pyproject = _toml(path / "pyproject.toml")
    project = pyproject.get("project")
    if isinstance(project, dict) and project.get("scripts"):
        return True
    poetry = (
        pyproject.get("tool", {}).get("poetry")
        if isinstance(pyproject.get("tool"), dict)
        else None
    )
    return bool(isinstance(poetry, dict) and poetry.get("scripts"))


_NODE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}


def _inherit_node_manager(root_det: Detection, det: Detection) -> None:
    """A workspace member has no lockfile of its own: it uses the root's manager.

    Without this the member would fall back to a guessed `npm`.
    """
    guessed = [
        p
        for p in det.package_managers
        if p.name == "npm"
        and p.lockfile is None
        and p.evidence[0].detector == "node.package_json"
    ]
    parents = [p for p in root_det.package_managers if p.name in _NODE_MANAGERS]
    if not guessed or len(parents) != 1:
        return
    parent = parents[0]
    guessed[0].name = parent.name
    guessed[0].lockfile = parent.lockfile
    guessed[0].evidence = [*parent.evidence, *guessed[0].evidence]


def _has_content(det: Detection) -> bool:
    return bool(det.manifests or det.runtimes or det.package_managers)


def discover(root: Path) -> Discovery:
    root = root.resolve()
    warnings: list[str] = []
    detections: dict[str, Detection] = {"root": detect_dir(root)}

    patterns = workspace_patterns(root, warnings)
    patterns += [*CONVENTIONAL, *CONVENTIONAL_GLOBS]
    for pattern in patterns:
        for path in expand(root, pattern):
            comp_id = path.relative_to(root).as_posix()
            if comp_id in detections:
                continue
            if len(detections) >= MAX_COMPONENTS:
                warnings.append(
                    f"components: more than {MAX_COMPONENTS} found, the rest are ignored"
                )
                break
            det = detect_dir(path, rel=f"{comp_id}/", component=comp_id)
            _inherit_node_manager(detections["root"], det)
            if _has_content(det):
                detections[comp_id] = det

    components = [
        Component(
            id=cid,
            path="." if cid == "root" else cid,
            runtimes=sorted({r.name for r in det.runtimes}),
            package_managers=sorted({p.name for p in det.package_managers}),
            scripts=dict(sorted(det.scripts.items())),
            runnable=_runnable(root if cid == "root" else root / cid, det),
        )
        for cid, det in sorted(
            detections.items(), key=lambda kv: (kv[0] != "root", kv[0])
        )
    ]

    ambiguities: list[Ambiguity] = []
    runnable = [c for c in components if c.runnable]
    if len(runnable) > 1:
        evidence = []
        for c in runnable:
            det = detections[c.id]
            if det.manifests:
                evidence.append(det.manifests[0].evidence[0])
        ambiguities.append(
            Ambiguity(
                id="amb-component",
                question="More than one runnable component found. Which should be set up?",
                options=[c.id for c in runnable],
                default=None,
                evidence=evidence,
            )
        )
    return Discovery(components, detections, ambiguities, warnings)
