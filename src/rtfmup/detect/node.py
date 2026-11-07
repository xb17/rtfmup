"""Node manifests: package.json, lockfiles, engines, packageManager."""

from __future__ import annotations

from rtfmup.detect._common import Detection, find_key_line

# lockfile -> package manager (PLAN §2.5). Order = preference when several exist.
LOCKFILES: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("package-lock.json", "npm"),
    ("yarn.lock", "yarn"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
)


def detect(d: Detection) -> None:
    text = d.read("package.json")
    if text is None:
        return
    d.manifest("package.json", "package_json", "node.package_json")
    data = d.json("package.json", text)

    for lockfile, pm in LOCKFILES:
        if d.exists(lockfile):
            d.package_manager(pm, lockfile, lockfile, 1, "node.lockfile")

    if data is None:
        return

    scripts = data.get("scripts")
    if isinstance(scripts, dict):
        for name, body in scripts.items():
            d.scripts[str(name)] = body if isinstance(body, str) else ""

    engines = data.get("engines")
    if isinstance(engines, dict) and isinstance(engines.get("node"), str):
        line = find_key_line(text, '"engines"', '"node"')
        d.runtime("node", engines["node"], "package.json", line, "node.engines")

    declared = data.get("packageManager")
    if isinstance(declared, str) and declared:
        pm = declared.split("@", 1)[0]
        line = find_key_line(text, '"packageManager"')
        d.package_manager(pm, None, "package.json", line, "node.package_manager_field")

    if not d.package_managers:
        d.package_manager("npm", None, "package.json", 1, "node.package_json")
