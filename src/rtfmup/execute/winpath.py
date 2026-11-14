"""Pick up PATH changes made by an installer during this run (Windows).

winget, Scoop and Chocolatey edit the registry PATH; this process keeps the PATH it
started with. After a runtime step we rebuild PATH from the registry so the next
step can find the tool that was just installed.
"""

from __future__ import annotations

import os
from collections.abc import Callable

_MACHINE = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
_USER = "Environment"


def _registry_path(hive: str, key: str) -> str:
    import winreg

    root = {"machine": winreg.HKEY_LOCAL_MACHINE, "user": winreg.HKEY_CURRENT_USER}[
        hive
    ]
    try:
        with winreg.OpenKey(root, key) as k:
            value, _ = winreg.QueryValueEx(k, "Path")
    except OSError:
        return ""
    return os.path.expandvars(value) if isinstance(value, str) else ""


def merge_paths(*parts: str) -> str:
    """Join PATH strings in order, dropping empty and duplicate entries."""
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        for entry in part.split(os.pathsep):
            entry = entry.strip()
            key = os.path.normcase(os.path.normpath(entry)) if entry else ""
            if key and key not in seen:
                seen.add(key)
                out.append(entry)
    return os.pathsep.join(out)


def refreshed_path(
    current: str,
    read: Callable[[str, str], str] | None = None,
) -> str:
    """Registry PATH (machine, then user) with the current PATH's extras kept after."""
    if read is None:
        if os.name != "nt":
            return current
        read = _registry_path
    return merge_paths(read("machine", _MACHINE), read("user", _USER), current)
