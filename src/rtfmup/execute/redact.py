"""Redaction of secret-like environment values from anything we print or log."""

from __future__ import annotations

import re
from collections.abc import Mapping

# Same rule as detect/envfile.py and readme/extract.py.
SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASS|PRIVATE", re.IGNORECASE)
MASK = "***"
_MIN_LEN = 3  # masking 1-2 character values would shred ordinary text


class Redactor:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        values = {
            v
            for k, v in (env or {}).items()
            if SECRET_NAME.search(k) and len(v) >= _MIN_LEN
        }
        # Longest first so a value that contains another is masked whole.
        self._values = sorted(values, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, MASK)
        return text
