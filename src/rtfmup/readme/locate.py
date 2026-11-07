"""Find the primary README. Read-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

MAX_README_BYTES = 2_000_000

# Search order: the repo root, then the places GitHub also renders from.
SEARCH_DIRS = ("", ".github", "docs")
# extension (lower-case, with dot) -> format. Earlier = preferred.
FORMATS: dict[str, Literal["markdown", "rst"]] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".rst": "rst",
}
UNSUPPORTED = {"", ".txt", ".adoc", ".asciidoc", ".org", ".textile"}


@dataclass
class ReadmeLocation:
    path: Path
    rel: str  # repo-relative, forward slashes
    format: Literal["markdown", "rst"]


@dataclass
class LocateResult:
    readme: ReadmeLocation | None = None
    warnings: list[str] = field(default_factory=list)


def _candidates(directory: Path) -> list[Path]:
    """README files in `directory`, matched case-insensitively on the real names."""
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    return [p for p in entries if p.stem.lower() == "readme" and p.is_file()]


def _inside(root: Path, p: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def locate(root: Path) -> LocateResult:
    result = LocateResult()
    root = root.resolve()
    for sub in SEARCH_DIRS:
        directory = root / sub if sub else root
        if not directory.is_dir():
            continue
        found = _candidates(directory)
        supported = [p for p in found if p.suffix.lower() in FORMATS]
        # Preference by extension order, then name.
        supported.sort(key=lambda p: (list(FORMATS).index(p.suffix.lower()), p.name))
        for p in supported:
            rel = p.relative_to(root).as_posix()
            if not _inside(root, p):
                result.warnings.append(
                    f"{rel}: resolves outside the repository, ignored"
                )
                continue
            if p.stat().st_size > MAX_README_BYTES:
                result.warnings.append(
                    f"{rel}: larger than {MAX_README_BYTES} bytes, ignored"
                )
                continue
            result.readme = ReadmeLocation(p, rel, FORMATS[p.suffix.lower()])
            return result
        for p in found:
            if p.suffix.lower() in UNSUPPORTED:
                rel = p.relative_to(root).as_posix()
                result.warnings.append(f"{rel}: unsupported README format")
    return result
