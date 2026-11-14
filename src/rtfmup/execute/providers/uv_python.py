"""uv provider: `uv python install` for Python only.

Syntax checked 2026-11-14 against docs.astral.sh/uv/concepts/python-versions and
`uv python install --help` (uv 0.13.7). It accepts `3.12`, `3.12.3` and quoted
specifiers such as `>=3.8,<3.10`, and installs into uv's own directory under the
user profile, so no admin is involved. The set of downloadable versions is frozen
per uv release; a spec newer than that fails at run time with uv's own message.
"""

from __future__ import annotations

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from rtfmup.execute.providers.base import runtime_step, tool_found
from rtfmup.host.probe import HostProfile
from rtfmup.plan.model import Step

_PLAIN = re.compile(r"^\d+(?:\.\d+){0,2}$")


def _uv_request(spec: str | None) -> str | None:
    """The argument for `uv python install`, or None if we can't express the spec.

    `None` spec means "no request": uv installs its latest.
    """
    if spec is None or not spec.strip():
        return ""
    text = spec.strip()
    if _PLAIN.match(text):
        return text
    try:
        SpecifierSet(text)
    except InvalidSpecifier:
        return None
    return text


class UvPythonProvider:
    name = "uv_python"

    def available(self, host: HostProfile) -> bool:
        return tool_found(host, "uv")

    def can_provide(self, runtime: str, spec: str | None) -> bool:
        return runtime == "python" and _uv_request(spec) is not None

    def prerequisite_steps(self, runtime: str, spec: str | None) -> list[Step]:
        return []

    def install_step(self, runtime: str, spec: str | None) -> Step:
        request = _uv_request(spec)
        if runtime != "python" or request is None:
            raise ValueError(f"uv cannot provide {runtime} {spec or ''}".strip())
        argv = ["uv", "python", "install", *([request] if request else [])]
        return runtime_step(
            self.name,
            runtime,
            request or "latest",
            argv,
            risk="medium",
            reasons=["downloads a Python build into uv's per-user directory"],
        )
