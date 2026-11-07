"""Pass 1 detectors: manifest-derived ground truth. Read-only, never executes."""

from __future__ import annotations

from pathlib import Path

from rtfmup.detect import dotnet, go, node, php, python, ruby, rust, versions
from rtfmup.detect._common import Detection

DETECTORS = (python, node, rust, go, ruby, php, dotnet, versions)


def detect_dir(root: Path, rel: str = "", component: str = "root") -> Detection:
    """Run every manifest detector on one directory."""
    d = Detection(root, rel=rel, component=component)
    for module in DETECTORS:
        module.detect(d)
    d.sort()
    return d


__all__ = ["DETECTORS", "Detection", "detect_dir"]
