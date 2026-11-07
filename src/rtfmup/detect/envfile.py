"""Env templates (.env.example and friends). Real `.env` files are never read."""

from __future__ import annotations

import re

from rtfmup.detect._common import Detection
from rtfmup.models import EnvVar

TEMPLATES = (
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
    "env.example",
    "example.env",
    "sample.env",
)

_ACTIVE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_COMMENTED = re.compile(r"^#\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$")
_SECRET = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASS|PRIVATE", re.IGNORECASE)


def parse_value(raw: str) -> tuple[str, str | None]:
    """Split `value  # comment` into (value, comment). Handles quotes."""
    rest = raw.lstrip()
    if rest[:1] in ("'", '"'):
        quote = rest[0]
        i = 1
        while i < len(rest):
            if rest[i] == "\\" and quote == '"':
                i += 2
                continue
            if rest[i] == quote:
                tail = rest[i + 1 :].strip()
                comment = tail[1:].strip() if tail.startswith("#") else None
                return rest[1:i], comment or None
            i += 1
        return rest[1:], None  # unterminated quote: take the rest literally
    if rest.startswith("#"):
        return "", rest[1:].strip() or None
    m = re.search(r"\s#", rest)
    if m:
        return rest[: m.start()].strip(), rest[m.end() :].strip() or None
    return rest.strip(), None


def parse_template(text: str) -> list[tuple[str, str, str | None, bool, int]]:
    """Return (name, value, comment, commented_out, line) for each variable."""
    out = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()  # also drops a trailing \r from CRLF files
        if not line:
            continue
        commented = line.startswith("#")
        m = (_COMMENTED if commented else _ACTIVE).match(line)
        if not m:
            continue
        value, comment = parse_value(m.group(2))
        out.append((m.group(1), value, comment, commented, n))
    return out


def detect(d: Detection) -> None:
    for name in TEMPLATES:
        text = d.read(name)
        if text is None:
            continue
        d.manifest(name, "env_template", "env.template")
        seen = {e.name for e in d.env}
        for var, value, comment, commented, line in parse_template(text):
            if var in seen:
                continue
            seen.add(var)
            if commented or value:
                required = "optional"
            else:
                required = "required"
            d.env.append(
                EnvVar(
                    name=var,
                    default=value or None,
                    required=required,
                    secret_like=bool(_SECRET.search(var)),
                    description=comment,
                    evidence=[d.ref(name, line, "env.template")],
                )
            )
