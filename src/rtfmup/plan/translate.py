"""D17: the bash -> PowerShell allowlist, and argv splitting.

Only the forms below are translated. Anything else that is bash-only stays a
`unix` step. Nothing here executes anything.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Translated:
    shell_text: str | None = None  # PowerShell to run
    env: dict[str, str] = field(default_factory=dict)  # `export K=V`


_EXPORT = re.compile(r"^export\s+(?P<pairs>[A-Za-z_]\w*=\S*(?:\s+[A-Za-z_]\w*=\S*)*)$")
_OPERATORS = re.compile(r"[|&;<>`]|\$\(")


def ps_quote(value: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def split_argv(text: str) -> list[str] | None:
    """`shlex.split` (posix) when it succeeds and there are no shell operators."""
    if _OPERATORS.search(text):
        return None
    try:
        argv = shlex.split(text, posix=True)
    except ValueError:
        return None
    return argv or None


def to_windows(text: str) -> Translated | None:
    """Translate an allowlisted bash command, or None if it is not translatable."""
    text = text.strip()
    if (m := _EXPORT.match(text)) and not _OPERATORS.search(text) and "$" not in text:
        env: dict[str, str] = {}
        try:
            pairs = shlex.split(m.group("pairs"))
        except ValueError:  # a quote opened on this line and closed on a later one
            return None
        for pair in pairs:
            key, _, value = pair.partition("=")
            env[key] = value
        return Translated(env=env)
    argv = split_argv(text)
    if not argv:
        return None
    cmd, args = argv[0], argv[1:]
    if cmd == "cp" and len(args) == 2 and not any(a.startswith("-") for a in args):
        src, dst = args
        return Translated(
            shell_text=f"Copy-Item -LiteralPath {ps_quote(src)} -Destination {ps_quote(dst)}"
        )
    if cmd == "mkdir" and args[:1] == ["-p"] and len(args) >= 2:
        dirs = args[1:]
        if any(d.startswith("-") for d in dirs):
            return None
        return Translated(
            shell_text="; ".join(
                f"New-Item -ItemType Directory -Force -Path {ps_quote(d)} | Out-Null"
                for d in dirs
            )
        )
    return None
