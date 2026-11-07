"""PHP manifests: composer.json (require.php), composer.lock."""

from __future__ import annotations

from rtfmup.detect._common import Detection, find_key_line


def detect(d: Detection) -> None:
    text = d.read("composer.json")
    if text is None:
        return
    d.manifest("composer.json", "composer_json", "php.composer_json")
    lock = "composer.lock" if d.exists("composer.lock") else None
    d.package_manager("composer", lock, lock or "composer.json", 1, "php.composer")
    data = d.json("composer.json", text)
    require = data.get("require") if data else None
    if isinstance(require, dict) and isinstance(require.get("php"), str):
        line = find_key_line(text, '"require"', '"php"')
        d.runtime("php", require["php"], "composer.json", line, "php.require")
