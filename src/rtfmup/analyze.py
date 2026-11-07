"""Orchestrator: Pass 1 (manifests) + Pass 2 (README) + Pass 3 (resolve) -> Analysis.

`analyze()` is read-only on the target, except for the D10 cache under
`<repo>/.rtfmup/` (excluded through `.git/info/exclude`, never `.gitignore`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from rtfmup import __version__
from rtfmup.detect.components import discover
from rtfmup.models import SCHEMA_VERSION, Analysis, SourceRef
from rtfmup.readme.classify import classify_all
from rtfmup.readme.extract import extract, merge_env
from rtfmup.readme.locate import locate
from rtfmup.readme.parse import Section, parse
from rtfmup.readme.rst import parse_rst
from rtfmup.resolve import find_ambiguities, find_conflicts

CACHE_DIR = ".rtfmup"
CACHE_FILE = "analysis.json"
_GIT_TIMEOUT = 15

T = TypeVar("T")


# --- git state -------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    """Read-only git query. Repo config that could run programs is switched off."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    cmd = [
        "git", "-C", str(root),
        "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        *args,
    ]  # fmt: skip
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def git_state(root: Path) -> tuple[str | None, bool | None]:
    """(HEAD sha, dirty). Both None when `root` is not a usable git work tree."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None or Path(top.strip()).resolve() != root.resolve():
        return (
            None,
            None,
        )  # not a repo root (or a subdirectory of one): no cache identity
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain")
    return (head.strip() if head else None), (
        bool(status.strip()) if status is not None else None
    )


# --- assembly --------------------------------------------------------------------


def _key(item: object) -> tuple[str, int, str]:
    ev: list[SourceRef] = getattr(item, "evidence", None) or []
    first = ev[0] if ev else None
    label = (
        getattr(item, "name", None)
        or getattr(item, "text", None)
        or getattr(item, "id", "")
    )
    return (
        first.file if first else "",
        (first.line_start or 0) if first else 0,
        str(label),
    )


def _sorted(items: list[T]) -> list[T]:
    return sorted(items, key=_key)


def _merge_ports(ports):
    """One Port per port number; evidence from every source is kept."""
    by_port: dict[int, object] = {}
    for p in _sorted(ports):
        got = by_port.get(p.port)
        if got is None:
            by_port[p.port] = p.model_copy(deep=True)
        else:
            got.evidence += p.evidence  # type: ignore[attr-defined]
            if got.component is None:  # type: ignore[attr-defined]
                got.component = p.component  # type: ignore[attr-defined]
    return list(by_port.values())


def _read_readme(
    root: Path,
) -> tuple[Section | None, str | None, str | None, list[str]]:
    located = locate(root)
    warnings = list(located.warnings)
    if located.readme is None:
        return None, None, None, warnings
    loc = located.readme
    try:
        text = loc.path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return None, None, None, [*warnings, f"{loc.rel}: cannot read ({exc})"]
    tree = parse_rst(text) if loc.format == "rst" else parse(text)
    warnings += [f"{loc.rel}: {w}" for w in tree.warnings]
    return tree, loc.rel, loc.format, warnings


def build_analysis(root: Path) -> Analysis:
    """Run all three passes on `root` (no caching)."""
    root = root.resolve()
    head, dirty = git_state(root)

    discovery = discover(root)
    det = discovery.merged(root)

    tree, readme_rel, readme_fmt, warnings = _read_readme(root)
    readme_commands = []
    ex = None
    if tree is not None and readme_rel is not None:
        ex = extract(tree, readme_rel)
        readme_commands = classify_all(ex.commands)

    commands = _sorted([*det.commands, *readme_commands])
    commands = [
        c.model_copy(update={"id": f"cmd-{i}"}) for i, c in enumerate(commands, 1)
    ]
    runtimes = _sorted([*det.runtimes, *(ex.runtimes if ex else [])])
    managers = _sorted(det.package_managers)
    env = merge_env(det.env, ex.env if ex else [])

    conflicts = find_conflicts(commands, runtimes, managers, root, discovery.components)
    ambiguities = find_ambiguities(
        commands, discovery.components, managers, root, discovery.ambiguities
    )

    return Analysis(
        rtfmup_version=__version__,
        root=str(root),
        git_head=head,
        git_dirty=dirty,
        readme=readme_rel,
        readme_format=readme_fmt,  # type: ignore[arg-type]
        components=discovery.components,
        runtimes=runtimes,
        package_managers=managers,
        manifests=_sorted(det.manifests),
        commands=commands,
        env=env,
        services=_sorted(det.services),
        external_accounts=_sorted(ex.external_accounts) if ex else [],
        ports=_merge_ports([*det.ports, *(ex.ports if ex else [])]),
        deployments=_sorted(det.deployments),
        has_devcontainer=det.has_devcontainer,
        has_docker=det.has_docker,
        conflicts=conflicts,
        ambiguities=ambiguities,
        warnings=sorted({*det.warnings, *warnings}),
    )


# --- cache (D10) --------------------------------------------------------------------


def cache_path(root: Path) -> Path:
    return root / CACHE_DIR / CACHE_FILE


def _exclude_cache_dir(root: Path) -> None:
    """Add `.rtfmup/` to .git/info/exclude (D9). Never touches tracked files."""
    info = root / ".git" / "info"
    if not (root / ".git").is_dir():
        return
    exclude = info / "exclude"
    try:
        info.mkdir(parents=True, exist_ok=True)
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if f"{CACHE_DIR}/" not in current.splitlines():
            sep = "" if current.endswith("\n") or not current else "\n"
            exclude.write_text(f"{current}{sep}{CACHE_DIR}/\n", encoding="utf-8")
    except OSError:
        pass  # best effort: a failed exclude only means `git status` shows .rtfmup/


def read_cache(root: Path, head: str, dirty: bool | None) -> Analysis | None:
    """The cached analysis, if (HEAD, clean, rtfmup version, schema version) all match."""
    if dirty is not False:
        return None
    try:
        cached = Analysis.model_validate_json(
            cache_path(root).read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError):
        return None
    ok = (
        cached.git_head == head
        and cached.git_dirty is False
        and cached.rtfmup_version == __version__
        and cached.schema_version == SCHEMA_VERSION
        and cached.root == str(root)
    )
    return cached if ok else None


def write_cache(root: Path, analysis: Analysis) -> None:
    if analysis.git_head is None or analysis.git_dirty is not False:
        return  # only clean, committed trees have a stable identity
    target = cache_path(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _exclude_cache_dir(root)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            analysis.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        os.replace(tmp, target)
    except OSError:
        pass  # caching is an optimization; never fail an analysis because of it


def analyze(root: Path | str, *, fresh: bool = False) -> Analysis:
    """Analyze a local directory, reusing the D10 cache unless `fresh`."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"not a directory: {root}")
    head, dirty = git_state(root)
    if not fresh and head is not None:
        cached = read_cache(root, head, dirty)
        if cached is not None:
            return cached
    analysis = build_analysis(root)
    write_cache(root, analysis)
    return analysis
