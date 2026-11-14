"""`.env` creation and guided fill-in (T3.4).

Rules:
- No `.env`: copy the template verbatim, then prompt for the `required` variables
  (`getpass` for secret-like ones). Optional variables keep the template's values.
- `.env` exists: it is NEVER overwritten and its existing lines are never changed.
  We read only the key names, and offer to append the keys it lacks.
- Values go into `.env` and nowhere else: not into messages, not into the result,
  not into logs. Everything we report is a variable NAME.
- The repo is untrusted: a template that resolves outside the repo, or a `.env`
  that is a symlink, is refused rather than followed.
"""

from __future__ import annotations

import getpass
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from rtfmup.detect.envfile import parse_template, parse_value
from rtfmup.models import EnvVar

_NEEDS_QUOTES = re.compile(r"""[\s#"'$`\\]""")


@dataclass
class EnvResult:
    """What happened, by variable name only."""

    created: bool = False
    filled: list[str] = field(default_factory=list)  # a value was written
    unfilled: list[str] = field(default_factory=list)  # required, still empty
    appended: list[str] = field(default_factory=list)  # added to an existing .env
    skipped: str | None = None  # why nothing was done, when that is the case

    @property
    def complete(self) -> bool:
        return self.skipped is None and not self.unfilled


class Prompter:
    """Terminal prompts. `read`/`secret` are injectable so tests need no terminal."""

    def __init__(
        self,
        *,
        interactive: bool = True,
        read: Callable[[str], str] = input,
        secret: Callable[[str], str] = getpass.getpass,
        out: IO[str] | None = None,
    ) -> None:
        self.interactive = interactive
        self._read = read
        self._secret = secret
        self._out = out

    def say(self, text: str = "") -> None:
        out = self._out or sys.stderr
        out.write(text + "\n")
        out.flush()

    def value(self, var: EnvVar) -> str | None:
        """The user's value, or None if they gave none (blank, EOF, Ctrl+C)."""
        if not self.interactive:
            return None
        label = f"{var.name}"
        if var.description:
            label += f" ({var.description})"
        try:
            if var.secret_like:
                answer = self._secret(f"{label} [hidden, Enter to skip]: ")
            else:
                answer = self._read(f"{label} [Enter to skip]: ")
        except (EOFError, KeyboardInterrupt):
            self.say()
            return None
        answer = answer.rstrip("\r\n")
        return answer or None

    def yes(self, question: str) -> bool:
        if not self.interactive:
            return False
        try:
            return self._read(question + " [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            self.say()
            return False


def format_value(value: str) -> str:
    """A dotenv-safe right-hand side. Single quotes are literal in every common
    parser, so prefer them; fall back to escaped double quotes."""
    if not value or not _NEEDS_QUOTES.search(value):
        return value
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _valid(value: str) -> bool:
    return "\n" not in value and "\r" not in value and "\0" not in value


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _read_text(path: Path) -> str:
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write_text(path: Path, text: str, *, exclusive: bool = False) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def defined_keys(text: str) -> set[str]:
    """Names assigned in an existing .env. Commented-out lines don't count.

    Values are parsed away immediately and never kept.
    """
    return {
        name
        for name, _value, _comment, commented, _line in parse_template(text)
        if not commented
    }


def _fill_line(line: str, name: str, value: str) -> str | None:
    """`NAME=` (any prefix/suffix) with its value set, or None if not this variable."""
    m = re.match(rf"^(\s*(?:export\s+)?{re.escape(name)}\s*=)(.*?)(\r?\n?)$", line)
    if not m:
        return None
    prefix, rest, eol = m.groups()
    _val, comment = parse_value(rest)
    tail = f"  # {comment}" if comment else ""
    return f"{prefix}{format_value(value)}{tail}{eol}"


def setup_env(
    repo_root: Path,
    template: str,
    variables: Sequence[EnvVar],
    prompter: Prompter,
    *,
    cwd: str = ".",
) -> EnvResult:
    """Create or extend `<cwd>/.env` from `template` (repo-relative path)."""
    repo_root = repo_root.resolve()
    template_path = repo_root / template
    target = (repo_root / cwd) / ".env"

    if not _within(template_path, repo_root) or not template_path.is_file():
        return EnvResult(skipped=f"template {template} is missing or outside the repo")
    if not _within(target.parent, repo_root):
        return EnvResult(skipped=f"{cwd} is outside the repo")
    if target.is_symlink():
        return EnvResult(skipped=".env is a symlink; refusing to write through it")

    template_text = _read_text(template_path)
    required = [v for v in variables if v.required == "required"]

    if target.exists():
        return _extend(target, template_text, variables, prompter)
    return _create(target, template_text, required, prompter)


def _create(
    target: Path, template_text: str, required: list[EnvVar], prompter: Prompter
) -> EnvResult:
    result = EnvResult()
    lines = template_text.splitlines(keepends=True)
    for var in required:
        value = prompter.value(var)
        if value is None or not _valid(value):
            if value is not None:
                prompter.say(
                    f"  {var.name}: a value can't contain a line break; skipped"
                )
            result.unfilled.append(var.name)
            continue
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            new = _fill_line(line, var.name, value)
            if new is not None:
                lines[i] = new
                result.filled.append(var.name)
                break
        else:
            result.unfilled.append(var.name)
    try:
        _write_text(target, "".join(lines), exclusive=True)  # never overwrite
    except FileExistsError:
        return EnvResult(skipped=".env appeared while setting up; left untouched")
    result.created = True
    prompter.say(f"Created {target.name} from the template.")
    if result.unfilled:
        prompter.say("Still empty: " + ", ".join(result.unfilled))
    return result


def _extend(
    target: Path,
    template_text: str,
    variables: Sequence[EnvVar],
    prompter: Prompter,
) -> EnvResult:
    existing = _read_text(target)
    have = defined_keys(existing)
    # A variable the template ships commented out is "off by default": appending it
    # would switch it on, so only active template variables count as missing.
    off = {n for n, _v, _c, commented, _l in parse_template(template_text) if commented}
    missing = [v for v in variables if v.name not in have and v.name not in off]
    if not missing:
        prompter.say(".env already has every variable from the template.")
        return EnvResult(skipped="nothing missing")

    names = ", ".join(v.name for v in missing)
    prompter.say(f".env exists and lacks: {names}")
    if not prompter.yes("Append the missing variables to the end of .env?"):
        prompter.say("Left .env untouched.")
        return EnvResult(
            skipped="declined",
            unfilled=[v.name for v in missing if v.required == "required"],
        )

    result = EnvResult()
    eol = _newline(existing)
    block = []
    for var in missing:
        if var.required == "required":
            value = prompter.value(var)
            if value is None or not _valid(value):
                if value is not None:
                    prompter.say(f"  {var.name}: no line breaks allowed; added empty")
                block.append(f"{var.name}=")
                result.unfilled.append(var.name)
                result.appended.append(var.name)
                continue
            block.append(f"{var.name}={format_value(value)}")
            result.filled.append(var.name)
        else:
            block.append(f"{var.name}={format_value(var.default or '')}")
        result.appended.append(var.name)
    lead = "" if not existing or existing.endswith(("\n", "\r")) else eol
    _write_text_append(target, lead + eol.join(block) + eol)
    prompter.say("Appended: " + ", ".join(result.appended))
    return result


def _write_text_append(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="") as fh:
        fh.write(text)
