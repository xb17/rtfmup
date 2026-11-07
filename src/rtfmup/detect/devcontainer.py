"""Devcontainer presence. The file is JSONC, so it is only located, not parsed."""

from __future__ import annotations

from rtfmup.detect._common import Detection


def detect(d: Detection) -> None:
    candidates = [".devcontainer/devcontainer.json", ".devcontainer.json"]
    sub = d.root / ".devcontainer"
    if sub.is_dir() and not sub.is_symlink():
        candidates += sorted(
            f"{p.parent.relative_to(d.root).as_posix()}/devcontainer.json"
            for p in sub.glob("*/devcontainer.json")
        )
    for name in candidates:
        if d.exists(name):
            d.has_devcontainer = True
            d.manifest(name, "devcontainer", "devcontainer.file")
