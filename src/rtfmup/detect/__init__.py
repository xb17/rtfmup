"""Pass 1 detectors: manifest-derived ground truth. Read-only, never executes."""

from __future__ import annotations

from pathlib import Path

from rtfmup.detect import (
    ci,
    containers,
    deploy,
    devcontainer,
    dotnet,
    envfile,
    go,
    node,
    php,
    python,
    ruby,
    rust,
    tasks,
    versions,
)
from rtfmup.detect._common import Detection

DETECTORS = (
    python,
    node,
    rust,
    go,
    ruby,
    php,
    dotnet,
    versions,
    containers,
    envfile,
    tasks,
    devcontainer,
    ci,
    deploy,
)


def detect_dir(root: Path, rel: str = "", component: str = "root") -> Detection:
    """Run every manifest detector on one directory."""
    d = Detection(root, rel=rel, component=component)
    for module in DETECTORS:
        module.detect(d)
    d.sort()
    return d


__all__ = ["DETECTORS", "Detection", "detect_dir"]
