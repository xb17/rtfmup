"""Go manifests: go.mod (go and toolchain directives), go.sum."""

from __future__ import annotations

import re

from rtfmup.detect._common import Detection

_GO = re.compile(r"^\s*go\s+(\S+)")
_TOOLCHAIN = re.compile(r"^\s*toolchain\s+(\S+)")


def detect(d: Detection) -> None:
    text = d.read("go.mod")
    if text is None:
        return
    d.manifest("go.mod", "go_mod", "go.go_mod")
    sums = "go.sum" if d.exists("go.sum") else None
    d.package_manager("go", sums, sums or "go.mod", 1, "go.go_mod")

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("//", 1)[0]
        if m := _GO.match(line):
            d.runtime("go", m.group(1), "go.mod", i, "go.go_directive")
        elif m := _TOOLCHAIN.match(line):
            d.runtime("go", m.group(1), "go.mod", i, "go.toolchain_directive")
