"""Failure diagnostics (T4.1): match a failed step's output against known patterns.

Pure text matching on the failing step's last 200 output lines. Each entry has a regex,
an explanation and a suggested fix. At most two diagnoses are shown, most specific
first (the table is ordered most specific first, and the order is the tiebreak).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

MAX_LINES = 200
MAX_DIAGNOSES = 2


@dataclass(frozen=True)
class Diagnosis:
    id: str
    explanation: str
    fix: str


@dataclass(frozen=True)
class _Pattern:
    id: str
    regex: re.Pattern[str]
    explanation: str
    fix: str


def _p(id: str, regex: str, explanation: str, fix: str) -> _Pattern:
    return _Pattern(
        id, re.compile(regex, re.IGNORECASE | re.MULTILINE), explanation, fix
    )


# Most specific first. The macOS and Linux entries (T5.6) come after the Windows
# ones that cannot collide with them and before the generic permission one.
PATTERNS: tuple[_Pattern, ...] = (
    _p(
        "msvc-build-tools",
        r"Microsoft Visual C\+\+ 14\.0 or greater is required|"
        r"MSBuild\.exe.*(?:not found|could not be found)|"
        r"gyp ERR! find VS|Could not find any Visual Studio installation|"
        r"unable to find (?:a )?(?:suitable )?Visual Studio",
        "A package needs to compile native code and the Microsoft C++ build tools "
        "are not installed.",
        "Install the Visual Studio Build Tools with the 'Desktop development with C++' "
        "workload (winget install Microsoft.VisualStudio.2022.BuildTools), then run "
        "`rtfmup up --resume`.",
    ),
    _p(
        "rust-linker",
        r"linker `?link\.exe`? not found|`?link\.exe`? not found|"
        r"error: linking with `link\.exe` failed",
        "Rust could not find the MSVC linker (link.exe).",
        "Install the Visual Studio Build Tools with the 'Desktop development with C++' "
        "workload, then run again.",
    ),
    _p(
        "docker-daemon",
        r"Cannot connect to the Docker daemon|error during connect:.*docker|"
        r"docker daemon is not running|failed to connect to the docker API|"
        r"open //\./pipe/docker\w*: The system cannot find the file",
        "Docker is installed but its daemon is not running.",
        "Start Docker Desktop, wait until it says it is running, then run again.",
    ),
    _p(
        "xcode-clt-missing",
        r"xcrun: error: invalid active developer path|"
        r"xcode-select: note: no developer tools were found|"
        r"xcode-select: error: no developer tools were found|"
        r"missing xcrun at: /Library/Developer/CommandLineTools",
        "macOS's Command Line Tools (compiler, git, make) are not installed or are "
        "out of date after a macOS update.",
        "Run `xcode-select --install`, finish the installer, then run "
        "`rtfmup up --resume`.",
    ),
    _p(
        "rosetta-missing",
        r"Bad CPU type in executable|bad CPU type in executable",
        "A program built for Intel Macs was started on Apple silicon and Rosetta 2 "
        "is not installed.",
        "Run `softwareupdate --install-rosetta --agree-to-license`, or install the "
        "arm64 build of that tool, then run `rtfmup up --resume`.",
    ),
    _p(
        "pep668-externally-managed",
        r"externally-managed-environment|"
        r"This environment is externally managed",
        "pip refuses to install into a Python that the operating system or Homebrew "
        "manages (PEP 668).",
        "Install into a virtual environment instead (`uv venv` then "
        "`uv pip install ...`, or `python3 -m venv .venv`). Do not use "
        "--break-system-packages.",
    ),
    _p(
        "npm-global-eacces",
        r"EACCES[\s\S]{0,300}?(?:/usr/local/lib/node_modules|/usr/lib/node_modules|"
        r"/usr/local/bin|/opt/homebrew/lib/node_modules)|"
        r"permission denied, (?:mkdir|access|open|symlink) '/usr/(?:local/)?"
        r"(?:lib|bin)",
        "npm tried to install a global package into a system directory this user "
        "cannot write to.",
        "Do not use sudo. Install Node with a per-user manager (mise, nvm, fnm) or "
        "point npm at your own directory: `npm config set prefix ~/.npm-global` and "
        "put `~/.npm-global/bin` on PATH, then run again.",
    ),
    _p(
        "build-tools-missing",
        r"unable to execute '?(?:gcc|cc|c\+\+|g\+\+)'?: No such file|"
        r"error: command '(?:gcc|cc|x86_64-linux-gnu-gcc|aarch64-linux-gnu-gcc)' "
        r"failed: No such file or directory|"
        r"(?:gcc|cc|g\+\+|make): (?:command )?not found|"
        r"No CMAKE_C_COMPILER could be found|"
        r"is gcc not installed\?|sh: \d*:? ?(?:gcc|make): not found",
        "A package needs to compile native code and no C compiler or make is "
        "installed.",
        "Install the build tools: `sudo apt install build-essential` (Debian, "
        'Ubuntu), `sudo dnf groupinstall "Development Tools"` (Fedora), '
        "`sudo pacman -S base-devel` (Arch). Then run `rtfmup up --resume`.",
    ),
    _p(
        "brew-not-on-path",
        r"(?:zsh|bash|sh): (?:command not found: brew|brew: (?:command )?not found)|"
        r"\bbrew: command not found",
        "Homebrew is installed but its directory is not on PATH, or it is not "
        "installed.",
        "If it is installed, add it to PATH for this session: "
        '`eval "$(/opt/homebrew/bin/brew shellenv)"` (Apple silicon; '
        "`/usr/local/bin/brew` on an Intel Mac), and put that line in your shell "
        "profile yourself (rtfmup never edits it). If not, install Homebrew from "
        "https://brew.sh.",
    ),
    _p(
        "port-in-use",
        r"EADDRINUSE|address already in use|"
        r"Only one usage of each socket address|"
        r"port is already allocated|bind: .*already in use",
        "The port this project wants is already used by another program.",
        "Stop the program using that port (rtfmup names it when it can; "
        "`lsof -nP -iTCP:<port> -sTCP:LISTEN` on macOS and Linux, "
        "`Get-NetTCPConnection -LocalPort <port>` on Windows), or change the "
        "project's port setting, then run again.",
    ),
    _p(
        "long-paths",
        r"Filename too long|ENAMETOOLONG|"
        r"path.*exceed(?:s|ed)? (?:the )?(?:maximum|MAX_PATH)|"
        r"The specified path, file name, or both are too long",
        "A path is longer than the Windows 260-character limit.",
        "Enable long paths (as administrator: `Set-ItemProperty "
        r"HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem LongPathsEnabled 1`; "
        "for git: `git config --global core.longpaths true`), or use a shorter folder.",
    ),
    _p(
        "execution-policy",
        r"running scripts is disabled on this system|"
        r"cannot be loaded because running scripts is disabled|"
        r"UnauthorizedAccess.*\.ps1|PSSecurityException",
        "PowerShell's execution policy blocks the script.",
        "Allow scripts for your user: `Set-ExecutionPolicy -Scope CurrentUser "
        "RemoteSigned`, then run again.",
    ),
    _p(
        "wrong-runtime-version",
        r"The engine \"node\" is incompatible|Unsupported engine|"
        r"requires? (?:a )?(?:different )?Python\b.*(?:<|>|=|not in)|"
        r"requires-python|Python \d+\.\d+ (?:is )?not supported|"
        r"requires rustc \d+\.\d+|rustc \d+\.\d+\.\d+ is not supported|"
        r"requires (?:Node|node)(?:\.js)? (?:version )?[<>=^~]|"
        r"requires go >= ?\d|"
        r"The current .NET SDK does not support targeting",
        "A tool or package needs a different runtime version than the one installed.",
        "Install the version the project asks for (see the plan's gaps and "
        "`rtfmup doctor`), then run `rtfmup up --resume`.",
    ),
    _p(
        "file-locked-or-denied",
        r"\bEACCES\b|\bEPERM\b|EBUSY|"
        r"PermissionError: \[WinError (?:5|32)\]|"
        r"The process cannot access the file because it is being used by another "
        r"process|Access is denied",
        "A file is locked by another program or needs permissions this user lacks.",
        "Close editors, terminals, dev servers and antivirus scans using the repo, "
        "and do not run from a protected folder (Program Files, a synced OneDrive "
        "folder), then run again.",
    ),
)


def diagnose(output: Sequence[str]) -> list[Diagnosis]:
    """Diagnoses for a failing step's output (only its last 200 lines are read)."""
    text = "\n".join(list(output)[-MAX_LINES:])
    found = [
        Diagnosis(p.id, p.explanation, p.fix) for p in PATTERNS if p.regex.search(text)
    ]
    return found[:MAX_DIAGNOSES]
