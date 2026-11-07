"""Pass 2: classify README commands (PLAN T1.9). Pure functions, no execution.

Order of evidence: command prefix first; heading context breaks ties for weak
prefix rules and fills `other`. `reason` records which rule fired.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rtfmup.models import Command

# --- flags -------------------------------------------------------------------

_FETCH = r"(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)"
_PIPE_TO_SHELL = [
    re.compile(
        rf"(?i)\b{_FETCH}\b.*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|iex|Invoke-Expression)\b"
    ),
    re.compile(r"(?i)\b(?:iex|Invoke-Expression)\b\s*[(\"'$]"),
    # `bash <(curl ...)` and `sh -c "$(curl ...)"` run downloaded text the same way
    re.compile(rf"(?i)\b(?:sh|bash|zsh)\s+<\(\s*{_FETCH}\b"),
    re.compile(rf"(?i)\b(?:sh|bash|zsh)\s+(?:-c\s+)?[\"']?\$\(\s*{_FETCH}\b"),
]
_SUDO = re.compile(r"(?:^|[\s;&|(])sudo(?:\s|$)")


def flags_for(text: str) -> list[str]:
    out = []
    if any(rx.search(text) for rx in _PIPE_TO_SHELL):
        out.append("pipe_to_shell")
    if _SUDO.search(text):
        out.append("sudo")
    return out


# --- shell syntax (D17) --------------------------------------------------------

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def needs_shell(text: str) -> bool:
    """True if the command uses operators an argv runner can't express (D17)."""
    if _ENV_ASSIGN.match(text.lstrip()):
        return True
    quote: str | None = None
    prev = ""
    for ch in text:
        if quote:
            if ch == quote and prev != "\\":
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "|&;<>`" or (ch == "(" and prev == "$"):
            return True
        prev = ch
    return False


def split_segments(text: str) -> list[str]:
    """Split on unquoted `&&`, `||`, `;` and `|` (for classification only)."""
    segs: list[str] = []
    cur: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == quote and (i == 0 or text[i - 1] != "\\"):
                quote = None
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif ch in "&|;":
            segs.append("".join(cur))
            cur = []
            while i + 1 < len(text) and text[i + 1] in "&|":
                i += 1
        else:
            cur.append(ch)
        i += 1
    segs.append("".join(cur))
    return [s.strip() for s in segs if s.strip()]


# --- category rules ------------------------------------------------------------

Rule = tuple[re.Pattern[str], str, str, bool]  # regex, category, reason, weak


def _r(pattern: str, category: str, reason: str, weak: bool = False) -> Rule:
    return (re.compile(pattern, re.IGNORECASE), category, reason, weak)


PKG_MGRS = r"(?:npm|pnpm|yarn|bun)"
SCRIPT_RUN = rf"{PKG_MGRS}\s+(?:run\s+)?"

# First match wins. Order matters: specific setup, test, lint, install, run, build, setup.
RULES: list[Rule] = [
    # setup that looks like something else
    _r(r"^pre-commit\s+install\b", "setup", "prefix:pre-commit install"),
    _r(r"^(?:cp|copy)\s+\S*\.env\S*", "setup", "prefix:cp .env"),
    _r(r"^(?:\S+\s+)*?(?:key:generate|db:setup|db:create|db:migrate|db:seed|db:reset)\b", "setup", "match:db/key task"),
    _r(r"\bmigrate\b|\bmakemigrations\b|\bcreatesuperuser\b|\bcollectstatic\b", "setup", "match:migrate"),
    _r(r"^(?:alembic\s+upgrade|prisma\s+(?:migrate|db\s+push|generate)|flyway\s+migrate|sequelize\S*\s+db:migrate)\b", "setup", "prefix:db migration"),
    _r(rf"^{SCRIPT_RUN}(?:setup|init|bootstrap|doctor|seed)\b", "setup", "script:setup"),
    _r(r"^(?:createdb|psql|mysql|sqlite3)\b", "setup", "prefix:database cli", True),
    # tests and lint come before install/run so `uv run pytest` and `npm run lint` land here
    _r(r"^(?:uv\s+run\s+|poetry\s+run\s+|python3?\s+-m\s+|npx\s+|pipenv\s+run\s+|bundle\s+exec\s+)?(?:pytest|py\.test|tox|nox|rspec|phpunit|vitest|jest|mocha)\b", "test", "prefix:test runner"),
    _r(rf"^{PKG_MGRS}\s+(?:run\s+)?(?:test|tests|test:\S+)\b", "test", "script:test"),
    _r(r"^(?:cargo|go|dotnet|mvn|gradle|\./gradlew|composer|make|bundle\s+exec\s+rake|rake|php\s+artisan)\s+(?:test|check)\b", "test", "prefix:test"),
    _r(r"^(?:uv\s+run\s+|poetry\s+run\s+|python3?\s+-m\s+|npx\s+|pipenv\s+run\s+|bundle\s+exec\s+)?(?:ruff|eslint|prettier|black|flake8|mypy|pylint|isort|pyright|rubocop|golangci-lint|gofmt|stylelint|biome)\b", "lint", "prefix:linter"),
    _r(rf"^{PKG_MGRS}\s+(?:run\s+)?(?:lint|lint:\S+|format|fmt|typecheck|type-check)\b", "lint", "script:lint"),
    _r(r"^cargo\s+(?:clippy|fmt)\b|^pre-commit\s+run\b|^make\s+(?:lint|fmt|format)\b", "lint", "prefix:lint"),
    # venv creation
    _r(r"^(?:uv\s+venv|python3?\s+-m\s+venv|py\s+-3?(?:\.\d+)?\s*-m\s+venv|virtualenv|conda\s+create|conda\s+activate|nvm\s+use|rustup\b|pyenv\s+(?:install|local|global))\b", "setup", "prefix:environment"),
    # install
    _r(rf"^{PKG_MGRS}\s+(?:install|i|ci|add)\b|^yarn\s*$|^bun\s+install\b", "install", "prefix:package install"),
    _r(r"^(?:pip3?|python3?\s+-m\s+pip|py\s+-m\s+pip|pipx|uv\s+pip)\s+install\b", "install", "prefix:pip install"),
    _r(r"^uv\s+(?:sync|add|pip|tool\s+install|python\s+install)\b", "install", "prefix:uv"),
    _r(r"^(?:poetry|pipenv|pdm|hatch)\s+(?:install|add|sync)\b|^conda\s+(?:install|env\s+create)\b", "install", "prefix:python env install"),
    _r(r"^cargo\s+(?:build|install|fetch)\b", "install", "prefix:cargo build"),
    _r(r"^go\s+(?:mod\s+download|mod\s+tidy|get|install)\b", "install", "prefix:go"),
    _r(r"^(?:bundle\s+install|bundle$|gem\s+install|composer\s+(?:install|require|update)|dotnet\s+(?:restore|tool\s+install)|dotnet\s+workload)\b", "install", "prefix:dependency install"),
    _r(r"^(?:mvn|\./mvnw)\s+(?:install|dependency:\S+)\b|^(?:gradle|\./gradlew)\s+dependencies\b", "install", "prefix:jvm install"),
    _r(r"^(?:apt|apt-get|aptitude|dnf|yum|pacman|apk|zypper|snap|port|nix-env|nix-shell)\b", "install", "prefix:system package"),
    _r(r"^brew\s+services\b|^(?:systemctl|service)\b", "setup", "prefix:service control"),
    _r(r"^(?:brew|choco|winget|scoop)\b", "install", "prefix:package manager"),
    _r(r"^(?:xcode-select|softwareupdate)\b", "install", "prefix:macos tooling"),
    _r(r"^git\s+(?:clone|submodule\s+update|lfs\s+install)\b", "install", "prefix:git clone"),
    _r(r"^(?:docker|podman)\s+(?:pull|image\s+pull)\b", "install", "prefix:docker pull"),
    _r(r"^make\s+install\b", "install", "prefix:make install"),
    # run
    _r(r"^(?:docker[- ]compose|docker\s+compose|podman[- ]compose)\s+(?:up|start|run)\b|^(?:docker|podman)\s+(?:run|start)\b", "run", "prefix:docker run"),
    _r(rf"^{PKG_MGRS}\s+(?:run\s+)?(?:dev|start|serve|watch|develop|preview)\b|^npx\s+(?:next|vite|nodemon|ts-node|tsx)\b", "run", "script:dev/start/serve"),
    _r(r"^(?:\S+\s+)*?run\s+(?:dev|start|serve)\b", "run", "match:run dev/start/serve"),
    _r(r"^(?:uvicorn|gunicorn|hypercorn|daphne|flask\s+run|fastapi\s+(?:dev|run)|streamlit\s+run|jupyter|django-admin\s+runserver|python3?\s+(?:-m\s+)?(?:\S+\s+)?runserver|python3?\s+\S*manage\.py\s+runserver)\b", "run", "prefix:server"),
    _r(r"^(?:cargo\s+run|go\s+run|dotnet\s+(?:run|watch)|deno\s+(?:run|task)|java\s+-jar|node\s+\S+|rails\s+(?:s|server)|bin/rails\s+(?:s|server)|bundle\s+exec\s+(?:rails\s+(?:s|server)|puma)|php\s+artisan\s+serve|php\s+-S|mix\s+phx\.server|\./gradlew\s+bootRun|mvn\s+spring-boot:run)\b", "run", "prefix:run"),
    _r(r"^(?:uv|poetry|pipenv)\s+run\b", "run", "prefix:env run", True),
    _r(r"^(?:python3?|py)\s+(?!-m\s+(?:venv|pip))\S*\.py\b", "run", "prefix:python script", True),
    _r(r"^python3?\s+-m\s+\S+", "run", "prefix:python -m", True),
    # build
    _r(r"^(?:tsc|webpack|rollup|esbuild|vite\s+build|next\s+build|gradle\s+build|\./gradlew\s+build|mvn\s+(?:package|compile)|dotnet\s+(?:build|publish)|go\s+build|cmake|msbuild|docker[- ]compose\s+build|docker\s+build)\b", "build", "prefix:build tool"),
    # trivial setup (before the generic "build" word match, so `mkdir -p build` is setup)
    _r(r"^(?:cd|chdir|mkdir|export|set|source|\.|chmod|touch|ln|setx|\$env:\S+)(?:\s|$)", "setup", "prefix:shell setup", True),
    _r(r"^(?:\./|\.\\)\S+\.(?:sh|ps1|bat|cmd)\b|^(?:bash|sh|zsh)\s+\S+\.sh\b", "setup", "prefix:script", True),
    _r(r"\bbuild\b", "build", "match:build"),
    _r(r"^make\s*$|^make\s+all\b", "build", "prefix:make", True),
]  # fmt: skip

# README heading -> category (handoff §3 dictionary, plus Build/Test/Contributing).
HEADING_CATEGORY: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"(?i)\b(?:install(?:ation|ing)?|prerequisites?|requirements?|dependencies|get the code|download)\b"), "install", "heading:install"),
    (re.compile(r"(?i)\b(?:set ?up|configur(?:e|ation|ing)|environment|initiali[sz]e|bootstrap)\b"), "setup", "heading:setup"),
    (re.compile(r"(?i)\b(?:quick ?start|getting started|usage|running|run|start(?:ing)?|serv(?:e|ing)|launch|deploy(?:ing)?)\b"), "run", "heading:run"),
    (re.compile(r"(?i)\b(?:build(?:ing)?)\b"), "build", "heading:build"),
    (re.compile(r"(?i)\b(?:test(?:s|ing)?)\b"), "test", "heading:test"),
    (re.compile(r"(?i)\b(?:contribut(?:e|ing|ors?)|development|developing|hacking)\b"), "other", "heading:dev-only"),
]  # fmt: skip

_VARIANTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)docker|container|compose|podman"), "docker"),
    (re.compile(r"(?i)\bwindows\b|\bwin(?:10|11)\b"), "windows"),
    (re.compile(r"(?i)\bmac(?:os)?\b|\bos ?x\b|homebrew"), "macos"),
    (re.compile(r"(?i)\blinux\b|ubuntu|debian|fedora|arch\b"), "linux"),
    (re.compile(r"(?i)from source|\bmanual(?:ly)?\b|\bnative\b|local development"), "native"),
]  # fmt: skip


def variant_for(section_path: list[str]) -> str | None:
    """Deepest heading that names a variant wins."""
    for title in reversed(section_path):
        for rx, name in _VARIANTS:
            if rx.search(title):
                return name
    return None


def heading_category(section_path: list[str]) -> tuple[str, str] | None:
    for title in reversed(section_path):
        for rx, cat, reason in HEADING_CATEGORY:
            if rx.search(title):
                return cat, f"{reason} ({title})"
    return None


# --- platform ------------------------------------------------------------------

_UNIX_WORDS = {
    "sudo", "apt", "apt-get", "aptitude", "dnf", "yum", "pacman", "apk", "zypper",
    "source", ".", "chmod", "chown", "ln", "bash", "sh", "zsh", "snap", "nix-env",
}  # fmt: skip
_WINDOWS_WORDS = {"winget", "choco", "scoop", "iex", "setx"}
_MACOS_WORDS = {"brew", "xcode-select", "softwareupdate"}
_LINUX_WORDS = {"systemctl", "service"}
_CMDLET = re.compile(r"^[A-Z][a-z]+-[A-Z][A-Za-z]+$")  # Get-Date, Invoke-WebRequest
_KV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")


def _segment_platform(seg: str) -> str:
    words = seg.split()
    if not words:
        return "any"
    first = words[0]
    if _ENV_ASSIGN.match(first) and len(words) > 1:
        first = words[1]
    low = first.lower()
    # Translatable to PowerShell (D17): cp a b, cd dir, mkdir [-p], export K=V.
    if low == "cp":
        return (
            "any"
            if all(not w.startswith("-") or w in ("-r", "-R") for w in words[1:])
            else "unix"
        )
    if low in ("cd", "mkdir"):
        return (
            "any"
            if all(not w.startswith("-") or w == "-p" for w in words[1:])
            else "unix"
        )
    if low == "export":
        return "any" if len(words) == 2 and _KV.match(words[1]) else "unix"
    if low in _UNIX_WORDS or re.fullmatch(r"\./\S+\.sh", first):
        return "unix"
    if low in _WINDOWS_WORDS or _CMDLET.match(first) or re.fullmatch(r"\.\\\S+", first):
        return "windows"
    if low in _MACOS_WORDS:
        return "macos"
    if low in _LINUX_WORDS:
        return "linux"
    return "any"


def platform_for(text: str, shell: str) -> str:
    if shell in ("powershell", "cmd"):
        return "windows"
    found = {_segment_platform(s) for s in split_segments(text)} - {"any"}
    if len(found) == 1:
        return found.pop()
    if not found:
        return "any"
    # Mixed (e.g. `sudo apt ... && brew ...`): the strictest claim is unix-only.
    return "unix" if "unix" in found else min(found)


# --- classification ----------------------------------------------------------


@dataclass
class Verdict:
    category: str
    reason: str
    weak: bool


_TRIVIAL = {
    "cd",
    "chdir",
    "export",
    "set",
    "source",
    ".",
    "mkdir",
    "chmod",
    "touch",
    "ln",
    "cp",
    "setx",
}
_ENV_PREFIX = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+")
_WRAPPERS = re.compile(r"^(?:sudo(?:\s+-\S+)*|env|time|nohup|exec)\s+")


def _strip_wrappers(seg: str) -> str:
    prev = None
    while prev != seg:
        prev = seg
        seg = _WRAPPERS.sub("", _ENV_PREFIX.sub("", seg.strip()), count=1).strip()
    return seg


def _match(seg: str) -> Verdict | None:
    seg = _strip_wrappers(seg)
    for rx, category, reason, weak in RULES:
        if rx.search(seg):
            return Verdict(category, reason, weak)
    return None


def category_for(
    text: str, section_path: list[str], flags: list[str]
) -> tuple[str, str]:
    """(category, reason): prefix first, heading context breaks ties and fills `other`."""
    if "pipe_to_shell" in flags:
        return "install", "flag:pipe_to_shell"
    segments = split_segments(text) or [text]
    verdicts = [(seg, _match(seg)) for seg in segments]
    # In a chain like `cd app && npm ci` the navigation step is not the point.
    meaningful = [
        (s, v)
        for s, v in verdicts
        if not (
            v
            and v.weak
            and _strip_wrappers(s).split()[:1]
            and _strip_wrappers(s).split()[0].lower() in _TRIVIAL
        )
    ] or verdicts
    verdict = next((v for _, v in meaningful if v), None)
    heading = heading_category(section_path)

    if verdict is None:
        if heading:
            return heading[0], heading[1] + " (no prefix rule)"
        return "other", "no rule matched"
    if (
        verdict.weak
        and heading
        and heading[0] != "other"
        and heading[0] != verdict.category
    ):
        # A script under "Installation" is setup work, not a package install.
        category = "setup" if heading[0] == "install" else heading[0]
        return category, f"{heading[1]} (overrides weak {verdict.reason})"
    return verdict.category, verdict.reason


def classify(cmd: Command) -> Command:
    """Return `cmd` with category, reason, variant, platform, flags and needs_shell set."""
    flags = list(dict.fromkeys([*cmd.flags, *flags_for(cmd.text)]))
    category, reason = category_for(cmd.text, cmd.section_path, flags)
    variant = variant_for(cmd.section_path)
    if variant is None and re.match(
        r"(?i)^(?:docker|podman)\b", _strip_wrappers(cmd.text)
    ):
        variant = "docker"  # `docker compose up` is the docker path wherever it appears
    return cmd.model_copy(
        update={
            "category": category,
            "reason": reason,
            "variant": variant,
            "platform": platform_for(cmd.text, cmd.shell),
            "flags": flags,
            "needs_shell": needs_shell(cmd.text),
        }
    )


def classify_all(commands: list[Command]) -> list[Command]:
    return [classify(c) for c in commands]
