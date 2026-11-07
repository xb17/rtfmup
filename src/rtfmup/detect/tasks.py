"""Task runners: Makefile targets, Justfile recipes, Taskfile tasks.

Parsed as text/YAML only. Nothing is ever run (PLAN §7.8). Entries go into
`Detection.scripts` as "make:<target>", "just:<recipe>", "task:<name>".
"""

from __future__ import annotations

import re

import yaml

from rtfmup.detect._common import MAX_BYTES, Detection

MAKEFILES = ("Makefile", "makefile", "GNUmakefile")
JUSTFILES = ("Justfile", "justfile", ".justfile")
TASKFILES = ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml", "taskfile.yaml")

_MAKE_TARGET = re.compile(
    r"^([A-Za-z0-9_][^\s:=#$%]*(?:\s+[A-Za-z0-9_][^\s:=#$%]*)*)\s*:(?![=:])"
)
_JUST_NAME = re.compile(r"^@?([A-Za-z_][\w-]*)")
_JUST_KEYWORDS = {"set", "alias", "import", "export", "mod", "unexport"}


def detect(d: Detection) -> None:
    for name in MAKEFILES:
        text = d.read(name)
        if text is not None:
            d.manifest(name, "makefile", "tasks.makefile")
            for target, body in parse_makefile(text).items():
                d.scripts.setdefault(f"make:{target}", body)
            break
    for name in JUSTFILES:
        text = d.read(name)
        if text is not None:
            d.manifest(name, "justfile", "tasks.justfile")
            for recipe, body in parse_justfile(text).items():
                d.scripts.setdefault(f"just:{recipe}", body)
            break
    for name in TASKFILES:
        text = d.read(name)
        if text is not None:
            d.manifest(name, "taskfile", "tasks.taskfile")
            _taskfile(d, name, text)
            break


def _collect(lines: list[str], names_at: dict[int, list[str]]) -> dict[str, str]:
    """Attach indented lines that follow a header line to its names."""
    out: dict[str, str] = {}
    starts = sorted(names_at)
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        body = []
        for raw in lines[start + 1 : end]:
            if raw[:1] in ("\t", " ") and raw.strip():
                body.append(raw.strip())
            elif raw.strip() and not raw.lstrip().startswith("#"):
                break
        for name in names_at[start]:
            out.setdefault(name, "\n".join(body))
    return out


def parse_makefile(text: str) -> dict[str, str]:
    lines = text.splitlines()
    names_at: dict[int, list[str]] = {}
    for i, raw in enumerate(lines):
        if raw[:1] in ("\t", " ", "#") or not raw.strip():
            continue
        m = _MAKE_TARGET.match(raw)
        if not m:
            continue
        names = [n for n in m.group(1).split() if not n.startswith(".")]
        if names:
            names_at[i] = names
    return _collect(lines, names_at)


def parse_justfile(text: str) -> dict[str, str]:
    lines = text.splitlines()
    names_at: dict[int, list[str]] = {}
    for i, raw in enumerate(lines):
        if raw[:1] in ("\t", " ", "#") or not raw.strip():
            continue
        m = _JUST_NAME.match(raw)
        if not m or m.group(1) in _JUST_KEYWORDS:
            continue
        rest = raw[m.end() :]
        if rest.lstrip().startswith(":="):  # variable assignment
            continue
        if re.search(r":(?!=)", rest):
            names_at[i] = [m.group(1)]
    return _collect(lines, names_at)


def _taskfile(d: Detection, name: str, text: str) -> None:
    if len(text) > MAX_BYTES:
        d.warnings.append(f"{d.relname(name)}: larger than {MAX_BYTES} bytes, skipped")
        return
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        d.warnings.append(
            f"{d.relname(name)}: invalid YAML ({str(exc).splitlines()[0]})"
        )
        return
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, dict):
        return
    for task, spec in tasks.items():
        body = ""
        if isinstance(spec, dict) and isinstance(spec.get("cmds"), list):
            body = "\n".join(c for c in spec["cmds"] if isinstance(c, str))
        elif isinstance(spec, str):
            body = spec
        d.scripts.setdefault(f"task:{task}", body)
