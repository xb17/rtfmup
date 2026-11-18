"""T2.3: risk rating for a plan step (PLAN §1 D8, §7).

`high`   pipe-to-shell, sudo/elevation, writes outside the repo, `git config
         --global`, system service changes, system package managers.
`medium` network installs (package managers, image pulls, clones, downloads) and
         anything that needs a shell.
`low`    everything else.

The rating looks at the command as the README wrote it (before any D17 translation)
and records every rule that fired, so the user sees why a step needs a closer look.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

Risk = str  # "low" | "medium" | "high"

_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    risk: Risk
    reason: str


def _r(pattern: str, risk: Risk, reason: str) -> _Rule:
    return _Rule(re.compile(pattern, re.IGNORECASE), risk, reason)


# A command position: start of the text or after a chain operator.
_CMD = r"(?:^|[;&|]\s*|&&\s*|\|\|\s*)(?:sudo\s+|env\s+)?"

RULES: tuple[_Rule, ...] = (
    # --- high ---
    _r(r"\bsudo\b|\bdoas\b|\bgsudo\b", "high", "uses sudo"),
    _r(r"-Verb\s+RunAs\b|\brunas\b", "high", "asks for elevation"),
    _r(r"--scope[ =]machine\b", "high", "installs machine-wide (needs admin)"),
    _r(
        r"git\s+config\s+(?:[^|;&]*\s)?--(?:global|system)\b",
        "high",
        "changes git config outside the repo",
    ),
    _r(
        rf"{_CMD}(?:systemctl|launchctl|service|net|sc(?:\.exe)?|schtasks|netsh)\s+"
        r"(?:start|stop|restart|enable|disable|daemon-reload|load|unload|create|config|"
        r"delete|bootstrap|bootout)\b|"
        rf"{_CMD}(?:Set|New|Start|Stop|Restart|Remove)-Service\b|"
        rf"{_CMD}pm2\s+startup\b",
        "high",
        "changes a system service",
    ),
    _r(
        rf"{_CMD}(?:apt|apt-get|aptitude|dnf|yum|pacman|zypper|apk|snap|port)\s+"
        r"(?:-\S+\s+)*(?:install|add|-S\S*|upgrade|remove)\b",
        "high",
        "system package manager (needs elevation)",
    ),
    _r(
        rf"{_CMD}choco(?:latey)?\s+(?:install|upgrade|uninstall)\b",
        "high",
        "Chocolatey needs an elevated shell",
    ),
    _r(
        rf"{_CMD}winget\s+(?:install|upgrade|uninstall)\b(?![^|;&]*--scope[ =]user)",
        "high",
        "winget may ask for elevation (no --scope user)",
    ),
    _r(
        rf"{_CMD}(?:setx|reg\s+(?:add|delete)|Set-ItemProperty\s+HK)",
        "high",
        "changes persistent system or user settings",
    ),
    # --- medium ---
    _r(
        rf"{_CMD}(?:npm|pnpm|yarn|bun)\s+(?:install|i|ci|add|update|upgrade|up|dlx|exec|x)\b|"
        rf"{_CMD}(?:npx|bunx|pnpx)\b",
        "medium",
        "downloads npm packages",
    ),
    _r(
        rf"{_CMD}(?:(?:python3?|py)\s+-m\s+)?pip3?\s+(?:install|download)\b|"
        rf"{_CMD}uv\s+(?:sync|add|pip\s+install|tool\s+install|python\s+install|venv)\b|"
        rf"{_CMD}uvx\b|"
        rf"{_CMD}(?:poetry|pipenv|pdm|hatch)\s+(?:install|add|update|sync|run)\b|"
        rf"{_CMD}conda\s+(?:install|create|env)\b",
        "medium",
        "downloads Python packages",
    ),
    _r(
        rf"{_CMD}cargo\s+(?:build|install|binstall|fetch|add|update|run|check)\b|"
        rf"{_CMD}go\s+(?:mod\s+download|get|install)\b|"
        rf"{_CMD}(?:bundle|gem)\s+(?:install|update)\b|"
        rf"{_CMD}composer\s+(?:install|update|require)\b|"
        rf"{_CMD}dotnet\s+(?:restore|build|tool\s+install|workload\s+install)\b",
        "medium",
        "downloads dependencies",
    ),
    _r(
        rf"{_CMD}(?:docker|podman)\s+(?:pull|build|run)\b|"
        rf"{_CMD}(?:docker|podman)\s+compose\b[^|;&]*?\b(?:up|pull|build|run)\b|"
        rf"{_CMD}(?:docker|podman)-compose\b[^|;&]*?\b(?:up|pull|build|run)\b",
        "medium",
        "pulls or builds container images",
    ),
    _r(
        rf"{_CMD}git\s+(?:clone|pull|fetch|submodule\s+update)\b",
        "medium",
        "fetches from a remote repository",
    ),
    _r(
        rf"{_CMD}(?:curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b",
        "medium",
        "downloads from the network",
    ),
    _r(
        rf"{_CMD}(?:winget\s+(?:install|upgrade)|scoop\s+(?:install|bucket\s+add|update)|brew\s+(?:install|upgrade|tap)|"
        rf"mise\s+(?:install|use)|asdf\s+(?:install|plugin)|nvm\s+install|fnm\s+install|"
        rf"volta\s+install|rustup\s+(?:install|toolchain|update|component))\b",
        "medium",
        "installs a toolchain",
    ),
)

# Absolute or home-relative paths: writes (or reads) outside the repo.
_OUTSIDE = re.compile(
    r"""(?x)
    (?:^|[\s='"(,])(?:
        ~(?:[/\\]|\s|$)                              # ~, ~/x
      | /(?!dev/null\b)[A-Za-z0-9_.-]+(?:/[^\s'"]*)? # /etc/x, /tmp
      | [A-Za-z]:[\\/]                               # C:\\x
      | %(?:APPDATA|LOCALAPPDATA|USERPROFILE|PROGRAMFILES|SYSTEMROOT|TEMP|HOMEPATH)%
      | \$env:(?:APPDATA|LOCALAPPDATA|USERPROFILE|ProgramFiles|SystemRoot|TEMP|HOME)\b
      | \$\{?HOME\}?
    )""",
    re.IGNORECASE,
)

# `/c`, `/s` ... Windows switches and URL paths must not look like absolute paths.
_WINDOWS_SWITCH = re.compile(r"(?:^|\s)/[A-Za-z?](?=\s|$)")


def _outside_repo(text: str) -> bool:
    cleaned = re.sub(r"[a-z][a-z0-9+.-]*://\S*", " ", text, flags=re.IGNORECASE)
    cleaned = _WINDOWS_SWITCH.sub(" ", cleaned)
    return bool(_OUTSIDE.search(cleaned))


def rate(
    text: str,
    *,
    flags: Sequence[str] = (),
    needs_shell: bool = False,
) -> tuple[Risk, list[str]]:
    """(risk, reasons) for one command."""
    risk: Risk = "low"
    reasons: list[str] = []

    def hit(level: Risk, reason: str) -> None:
        nonlocal risk
        if reason not in reasons:
            reasons.append(reason)
        if _ORDER[level] > _ORDER[risk]:
            risk = level

    if "pipe_to_shell" in flags:
        hit("high", "pipes a download into a shell")
    if "sudo" in flags:
        hit("high", "uses sudo")
    if "writes_outside_repo" in flags:
        hit("high", "writes outside the repo")
    for rule in RULES:
        if rule.pattern.search(text):
            hit(rule.risk, rule.reason)
    if _outside_repo(text):
        hit("high", "touches a path outside the repo")
    if needs_shell:
        hit("medium", "needs a shell (pipes, chaining or redirects)")
    if "network_install" in flags and risk == "low":
        hit("medium", "installs from the network")
    return risk, reasons
