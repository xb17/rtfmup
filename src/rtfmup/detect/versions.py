"""Runtime pins from version files: .nvmrc, .node-version, .python-version,
.ruby-version, .tool-versions, mise.toml."""

from __future__ import annotations

from rtfmup.detect._common import Detection, find_key_line

# file -> (runtime, manifest kind)
SINGLE_VALUE_FILES: dict[str, tuple[str, str]] = {
    ".nvmrc": ("node", "nvmrc"),
    ".node-version": ("node", "node_version"),
    ".python-version": ("python", "python_version"),
    ".ruby-version": ("ruby", "ruby_version"),
}

# Names used by asdf/mise, mapped to our runtime names.
TOOL_NAMES: dict[str, str] = {
    "node": "node",
    "nodejs": "node",
    "python": "python",
    "ruby": "ruby",
    "go": "go",
    "golang": "go",
    "rust": "rust",
    "java": "java",
    "php": "php",
    "dotnet": "dotnet",
    "dotnet-core": "dotnet",
    "bun": "bun",
    "deno": "deno",
}


def detect(d: Detection) -> None:
    for name, (runtime, kind) in SINGLE_VALUE_FILES.items():
        text = d.read(name)
        if text is None:
            continue
        d.manifest(name, kind, "versions.single_file")
        for i, raw in enumerate(text.splitlines(), start=1):
            value = raw.split("#", 1)[0].strip()
            if value:
                d.runtime(runtime, value, name, i, "versions.single_file")
                break

    _tool_versions(d)
    for name in ("mise.toml", ".mise.toml"):
        _mise(d, name)


def _tool_versions(d: Detection) -> None:
    text = d.read(".tool-versions")
    if text is None:
        return
    d.manifest(".tool-versions", "tool_versions", "versions.tool_versions")
    for i, raw in enumerate(text.splitlines(), start=1):
        parts = raw.split("#", 1)[0].split()
        if len(parts) >= 2 and parts[0] in TOOL_NAMES:
            d.runtime(
                TOOL_NAMES[parts[0]],
                parts[1],
                ".tool-versions",
                i,
                "versions.tool_versions",
            )


def _mise(d: Detection, name: str) -> None:
    text = d.read(name)
    if text is None:
        return
    d.manifest(name, "mise_toml", "versions.mise")
    data = d.toml(name, text)
    tools = data.get("tools") if data else None
    if not isinstance(tools, dict):
        return
    for tool, value in tools.items():
        runtime = TOOL_NAMES.get(tool)
        if runtime is None:
            continue
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, dict):
            value = value.get("version")
        if not isinstance(value, str):
            continue
        line = find_key_line(text, "[tools]", tool)
        d.runtime(runtime, value, name, line, "versions.mise")
