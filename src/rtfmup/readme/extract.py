"""Pass 2: README extraction from the Section tree (PLAN T1.8).

Heuristic and read-only. Nothing here executes anything. Commands come back
unclassified (`category="other"`); `classify.py` (T1.9) assigns categories.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from rtfmup.models import (
    Command,
    EnvVar,
    ExternalAccount,
    Port,
    Runtime,
    SourceRef,
)
from rtfmup.readme.parse import Block, Section

# --- commands ----------------------------------------------------------------

# fence info -> Command.shell
SHELL_LANGS: dict[str, str] = {
    "bash": "bash",
    "sh": "sh",
    "shell": "sh",
    "zsh": "zsh",
    "console": "console",
    "shell-session": "console",
    "shellsession": "console",
    "powershell": "powershell",
    "pwsh": "powershell",
    "ps1": "powershell",
    "cmd": "cmd",
    "bat": "cmd",
    "batch": "cmd",
}
_PS_PROMPT = re.compile(r"^(?:PS\s*[^>\n]*>)\s*")  # "PS>" and "PS C:\x>"
_CMD_PROMPT = re.compile(r"^[A-Za-z]:\\[^>\n]*>\s*")
_DOLLAR_PROMPT = re.compile(r"^\$\s+")
_ANGLE_PROMPT = re.compile(r"^>\s+")
_CONTINUATION = {"powershell": "`", "cmd": "^"}  # everything else uses "\"
_INSTALL_HEADING = re.compile(
    r"(?i)install|set ?up|getting started|usage|quick ?start|run|build|develop"
)
_PLACEHOLDER = re.compile(
    r"<[A-Za-z][\w.-]*(?:[ _-][\w.-]+)*>"  # <your-token>
    r"|\bYOUR_[A-Z0-9_]+\b"  # YOUR_API_KEY
    r"|(?<![$\w])\{[A-Za-z_][\w-]*\}"  # {name}, but not ${VAR} or {a,b}
)


@dataclass
class Extraction:
    commands: list[Command] = field(default_factory=list)
    runtimes: list[Runtime] = field(default_factory=list)
    env: list[EnvVar] = field(default_factory=list)
    external_accounts: list[ExternalAccount] = field(default_factory=list)
    ports: list[Port] = field(default_factory=list)


def _prompt_kind(line: str) -> str | None:
    s = line.lstrip()
    if _PS_PROMPT.match(s):
        return "ps"
    if _CMD_PROMPT.match(s):
        return "cmd"
    if _DOLLAR_PROMPT.match(s):
        return "dollar"
    if _ANGLE_PROMPT.match(s):
        return "angle"
    return None


def _strip_prompt(line: str) -> str:
    s = line.lstrip()
    for rx in (_PS_PROMPT, _CMD_PROMPT, _DOLLAR_PROMPT, _ANGLE_PROMPT):
        if rx.match(s):
            return rx.sub("", s, count=1)
    return s


def _fence_plan(block: Block, sec: Section) -> tuple[str, str, bool] | None:
    """Decide whether a code block holds commands: (shell, confidence, strip_all_prompts)."""
    lang = block.lang
    if lang in SHELL_LANGS:
        return SHELL_LANGS[lang], "medium", False
    if lang:
        return None  # a known non-shell language (python, json, ...)
    lines = [ln for ln in block.text.splitlines() if ln.strip()]
    kinds = [_prompt_kind(ln) for ln in lines]
    prompted = [k for k in kinds if k]
    # Most lines carry a prompt, or the block opens with a prompted command and at
    # least half do (`$ flask run` followed by one line of output).
    if lines and (
        len(prompted) * 2 > len(lines) or (kinds[0] and len(prompted) * 2 >= len(lines))
    ):
        dominant = max(set(prompted), key=prompted.count)
        shell = {"ps": "powershell", "cmd": "cmd", "dollar": "console"}.get(
            dominant, "unknown"
        )
        return shell, "low", True
    # rST bare `::` blocks: only under install-type headings, always low confidence.
    # rST bare `::` blocks and unlabeled Markdown indented blocks (D18 parity):
    # only under install-type headings, always low confidence.
    if (block.low_confidence or block.kind == "code_block") and any(
        _INSTALL_HEADING.search(t) for t in sec.path
    ):
        return "unknown", "low", False
    return None


def _logical_commands(
    block: Block, shell: str, all_prompts: bool
) -> list[tuple[str, int, int]]:
    """(text, first physical index, last physical index) for each command."""
    marker = _CONTINUATION.get(shell, "\\")
    console = shell == "console"
    out: list[tuple[str, int, int]] = []
    pending: str | None = None
    first = 0
    for i, raw in enumerate(block.text.splitlines()):
        line = raw.rstrip()
        if pending is not None:
            # A continuation line; console PS2 prompts ("> ") are dropped.
            part = (
                _ANGLE_PROMPT.sub("", line.lstrip(), count=1)
                if console
                else line.strip()
            )
            joined = pending + " " + part.strip()
            if joined.endswith(marker):
                pending = joined[: -len(marker)].rstrip()
                continue
            out.append((joined.strip(), first, i))
            pending = None
            continue
        if not line.strip():
            continue
        kind = _prompt_kind(line)
        if (console or all_prompts) and kind is None:
            continue  # output, not a command
        # Which prompts count depends on the fence: `> ` is only a prompt in
        # console-style blocks, `$ ` is a prompt almost anywhere.
        strip = {
            "console": {"ps", "cmd", "dollar", "angle"},
            "powershell": {"ps"},
            "cmd": {"cmd"},
        }.get(shell, {"dollar"})
        if all_prompts:
            strip = {"ps", "cmd", "dollar", "angle"}
        text = _strip_prompt(line) if kind in strip else line.strip()
        stripped = text.strip()
        if (
            not stripped
            or stripped.startswith(("#", "::"))
            or stripped.upper().startswith("REM ")
        ):
            continue
        if stripped.endswith(marker):
            pending, first = stripped[: -len(marker)].rstrip(), i
            continue
        out.append((stripped, i, i))
    if pending:
        out.append((pending, first, first + 1))
    return out


# --- versions ----------------------------------------------------------------

_VERSION = re.compile(
    r"(?i)(?<![\w.])(node(?:\.js)?|python|ruby|golang|go|java|rust|php|dotnet|\.net)"
    r"\s*v?(\d+(?:\.(?:\d+|x))*\+?)"
)
_RUNTIME_NAMES = {
    "node": "node",
    "node.js": "node",
    "python": "python",
    "ruby": "ruby",
    "go": "go",
    "golang": "go",
    "java": "java",
    "rust": "rust",
    "php": "php",
    "dotnet": "dotnet",
    ".net": "dotnet",
}
_PREREQ_HEADING = re.compile(
    r"(?i)prerequisite|requirement|before you begin|dependencies"
)
_REQUIRE_WORD = re.compile(r"(?i)\b(requires?|prerequisites?|needs?|supports?)\b")


# --- env vars ----------------------------------------------------------------

_ENV_NAME = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
# Assignment forms are unambiguous even without an underscore (PORT=3000, set DEBUG=1).
_ENV_ASSIGN = re.compile(
    r"(?:^|[\s;&|])(?:export\s+|set\s+|\$env:)?([A-Z][A-Z0-9_]*)\s*=(?!=)", re.MULTILINE
)
_ENV_STOPLIST = {
    "UTF_8", "UTF_16", "UTF_32", "SHA_1", "SHA_256", "SHA_512", "ISO_8601", "CI_CD",
    "README_MD", "TODO_LIST", "E_G", "I_E",
    "HTTP_OK", "HTTP_CREATED", "HTTP_NO_CONTENT", "HTTP_BAD_REQUEST", "HTTP_UNAUTHORIZED",
    "HTTP_FORBIDDEN", "HTTP_NOT_FOUND", "HTTP_INTERNAL_SERVER_ERROR", "HTTP_STATUS",
}  # fmt: skip
_ENV_ASSIGN_STOP = {"PS", "OK", "ID", "PATH_INFO"}
_ENV_FENCE_LANGS = set(SHELL_LANGS) | {
    "", "env", "dotenv", "ini", "properties", "text", "txt", "yaml", "yml", "toml",
    "dockerfile", "docker", "json",
}  # fmt: skip
_SECRET = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASS|PRIVATE", re.IGNORECASE)


def _env_ok(name: str) -> bool:
    return name not in _ENV_STOPLIST and not re.fullmatch(r"HTTP_\d+", name)


def env_names(text: str, *, allow_assign: bool) -> list[str]:
    """Env var names in `text`: underscore names, plus `NAME=` assignments."""
    names = [n for n in _ENV_NAME.findall(text) if _env_ok(n)]
    if allow_assign:
        names += [
            n
            for n in _ENV_ASSIGN.findall(text)
            if n not in _ENV_ASSIGN_STOP and _env_ok(n)
        ]
    seen: dict[str, None] = {}
    for n in names:
        seen.setdefault(n)
    return list(seen)


# --- ports / accounts ----------------------------------------------------------

_PORT = re.compile(
    r"(?:(?P<scheme>https?)://)?(?P<host>localhost|127\.0\.0\.1|0\.0\.0\.0):(?P<port>\d{2,5})\b"
)
_ACCOUNT = re.compile(
    r"(?i)sign ?up|api key|token|account|register|credentials|console|dashboard"
)


def _env_row(
    rows: list[list[str]], spans: set[str]
) -> list[tuple[str, str | None, str, str | None]]:
    """Env var rows of a table: (name, description, required, default)."""
    if len(rows) < 2:
        return []
    header = [h.strip().lower() for h in rows[0]]

    def col(rx: str) -> int | None:
        return next((i for i, h in enumerate(header) if re.search(rx, h)), None)

    req_col, desc_col, def_col = (
        col(r"required|optional"),
        col(r"descr|purpose|meaning|note"),
        col(r"default"),
    )
    out = []
    for row in rows[1:]:
        if not row:
            continue
        name = row[0].strip().strip("`")
        if not _ENV_NAME.fullmatch(name) and not (
            name in spans and re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
        ):
            continue
        if not _env_ok(name):
            continue
        desc = None
        if desc_col is not None and desc_col < len(row):
            desc = row[desc_col].strip() or None
        elif len(row) > 1 and def_col != len(row) - 1 and req_col != len(row) - 1:
            desc = row[-1].strip() or None
        required = "unknown"
        if req_col is not None and req_col < len(row):
            v = row[req_col].strip().lower()
            is_optional_col = (
                "optional" in header[req_col] and "required" not in header[req_col]
            )
            yes = v in ("yes", "true", "y", "✓", "✔", "required", "x", "✅")
            no = v in ("no", "false", "n", "optional", "", "-", "✗", "❌")
            if yes:
                required = "optional" if is_optional_col else "required"
            elif no and v != "":
                required = "required" if is_optional_col else "optional"
        default = None
        if (
            def_col is not None
            and def_col < len(row)
            and row[def_col].strip() not in ("", "-", "—", "none")
        ):
            default = row[def_col].strip().strip("`")
        out.append((name, desc, required, default))
    return out


def extract(root: Section, readme: str) -> Extraction:
    """Run every README extractor over a parsed Section tree."""
    ex = Extraction()
    env: dict[str, EnvVar] = {}
    seen_ports: set[tuple[int, int]] = set()
    seen_urls: set[str] = set()
    seen_versions: set[tuple[str, str, int]] = set()

    def ref(block_line: int, end: int, detector: str) -> SourceRef:
        return SourceRef(
            file=readme, line_start=block_line, line_end=end, detector=detector
        )

    def add_env(name: str, line: int, end: int, detector: str, **kw: object) -> None:
        got = env.get(name)
        evidence = ref(line, end, detector)
        if got is None:
            env[name] = EnvVar(
                name=name,
                default=kw.get("default"),  # type: ignore[arg-type]
                required=kw.get("required", "unknown"),  # type: ignore[arg-type]
                secret_like=bool(_SECRET.search(name)),
                description=kw.get("description"),  # type: ignore[arg-type]
                evidence=[evidence],
            )
            return
        got.evidence.append(evidence)
        if got.description is None and kw.get("description"):
            got.description = kw["description"]  # type: ignore[assignment]
        if got.required == "unknown" and kw.get("required", "unknown") != "unknown":
            got.required = kw["required"]  # type: ignore[assignment]
        if got.default is None and kw.get("default"):
            got.default = kw["default"]  # type: ignore[assignment]

    def add_ports(text: str, line: int, end: int) -> None:
        for m in _PORT.finditer(text):
            port = int(m.group("port"))
            if not 1 <= port <= 65535 or (port, line) in seen_ports:
                continue
            seen_ports.add((port, line))
            scheme = m.group("scheme") or "http"
            ex.ports.append(
                Port(
                    port=port,
                    url=f"{scheme}://{m.group('host')}:{port}",
                    evidence=[ref(line, end, "readme.port")],
                )
            )

    for sec, blk in root.walk_blocks():
        kind = blk.kind
        if kind in ("fence", "code_block"):
            plan = _fence_plan(blk, sec)
            if plan:
                shell, confidence, all_prompts = plan
                detector = (
                    "readme.literal_block"
                    if blk.low_confidence or kind == "code_block"
                    else "readme.fence"
                )
                start = blk.content_line_start
                for text, i0, i1 in _logical_commands(blk, shell, all_prompts):
                    ex.commands.append(
                        Command(
                            id="cmd-0",
                            text=text,
                            shell=shell,  # type: ignore[arg-type]
                            category="other",
                            reason="unclassified",
                            section_path=list(sec.path),
                            flags=["placeholder"] if _PLACEHOLDER.search(text) else [],
                            confidence=confidence,  # type: ignore[arg-type]
                            evidence=[ref(start + i0, start + i1, detector)],
                        )
                    )
            if blk.lang in _ENV_FENCE_LANGS:
                for name in env_names(blk.text, allow_assign=True):
                    line = _line_of(blk, name)
                    add_env(name, line, line, "readme.env")
            add_ports(blk.text, blk.content_line_start, blk.line_end)
            continue

        if kind in ("paragraph", "list_item"):
            text = blk.text
            add_ports(text, blk.line_start, blk.line_end)
            for name in {
                n for s in blk.code_spans for n in env_names(s, allow_assign=True)
            }:
                add_env(name, blk.line_start, blk.line_end, "readme.env")
            in_prereq = any(_PREREQ_HEADING.search(t) for t in sec.path)
            if in_prereq or kind == "list_item" or _REQUIRE_WORD.search(text):
                for m in _VERSION.finditer(text):
                    runtime = _RUNTIME_NAMES[m.group(1).lower()]
                    key = (runtime, m.group(2), blk.line_start)
                    if key in seen_versions:
                        continue
                    seen_versions.add(key)
                    ex.runtimes.append(
                        Runtime(
                            name=runtime,  # type: ignore[arg-type]
                            version_spec=m.group(2),
                            confidence="medium",
                            evidence=[
                                ref(blk.line_start, blk.line_end, "readme.version")
                            ],
                        )
                    )
            _accounts(ex, blk, text, seen_urls, ref)
        elif kind == "table":
            spans = set(blk.code_spans)
            for name, desc, required, default in _env_row(blk.rows, spans):
                add_env(
                    name, blk.line_start, blk.line_end, "readme.env_table",
                    description=desc, required=required, default=default,
                )  # fmt: skip
            add_ports(
                " ".join(c for r in blk.rows for c in r), blk.line_start, blk.line_end
            )
            _accounts(ex, blk, " ".join(c for r in blk.rows for c in r), seen_urls, ref)

    ex.env = list(env.values())
    return ex


def _line_of(blk: Block, needle: str) -> int:
    for i, ln in enumerate(blk.text.splitlines()):
        if needle in ln:
            return blk.content_line_start + i
    return blk.line_start


def _accounts(ex: Extraction, blk: Block, text: str, seen: set[str], ref) -> None:
    for link in blk.links:
        parsed = urlparse(link.href)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or link.href in seen
        ):
            continue
        if not (_ACCOUNT.search(link.text) or _ACCOUNT.search(text)):
            continue
        seen.add(link.href)
        related = sorted(
            {n for s in blk.code_spans for n in env_names(s, allow_assign=False)}
        )
        ex.external_accounts.append(
            ExternalAccount(
                name=link.text or parsed.netloc,
                url=link.href,
                related_env=related,
                evidence=[ref(blk.line_start, blk.line_end, "readme.link")],
            )
        )


def merge_env(template: list[EnvVar], readme: list[EnvVar]) -> list[EnvVar]:
    """Merge README env vars into the .env template's (T1.5). The template wins.

    README-only names stay `required="unknown"` unless a README table said more.
    A name in both keeps the template's required/default and gains README evidence
    and, if it had none, the README description.
    """
    merged = {e.name: e.model_copy(deep=True) for e in template}
    for e in readme:
        got = merged.get(e.name)
        if got is None:
            merged[e.name] = e.model_copy(deep=True)
            continue
        got.evidence += e.evidence
        if got.description is None:
            got.description = e.description
    return sorted(
        merged.values(),
        key=lambda v: (v.evidence[0].file, v.evidence[0].line_start or 0, v.name),
    )
