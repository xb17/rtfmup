"""`.NET` manifests: *.csproj, *.sln, global.json."""

from __future__ import annotations

import re

from rtfmup.detect._common import Detection, find_key_line

_TFM = re.compile(r"<TargetFrameworks?>\s*([^<]+?)\s*</TargetFrameworks?>")


def detect(d: Detection) -> None:
    projects = sorted(p.name for p in d.root.glob("*.csproj") if p.is_file())
    solutions = sorted(p.name for p in d.root.glob("*.sln") if p.is_file())
    for name in projects:
        d.manifest(name, "csproj", "dotnet.csproj")
        _target_framework(d, name)
    for name in solutions:
        d.manifest(name, "sln", "dotnet.sln")
    if projects or solutions:
        first = (projects or solutions)[0]
        lock = "packages.lock.json" if d.exists("packages.lock.json") else None
        d.package_manager("dotnet", lock, lock or first, 1, "dotnet.project")

    _global_json(d)


def _target_framework(d: Detection, name: str) -> None:
    text = d.read(name)
    if text is None:
        return
    for i, raw in enumerate(text.splitlines(), start=1):
        if m := _TFM.search(raw):
            d.runtime("dotnet", m.group(1), name, i, "dotnet.target_framework")
            return


def _global_json(d: Detection) -> None:
    text = d.read("global.json")
    if text is None:
        return
    d.manifest("global.json", "global_json", "dotnet.global_json")
    data = d.json("global.json", text)
    sdk = data.get("sdk") if data else None
    if isinstance(sdk, dict) and isinstance(sdk.get("version"), str):
        line = find_key_line(text, '"sdk"', '"version"')
        d.runtime(
            "dotnet", sdk["version"], "global.json", line, "dotnet.global_json_sdk"
        )
