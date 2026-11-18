"""HostProfile: what OS this is and which tools are installed.

Read-only: every probe is a version query with a short timeout, run in parallel.
Executables are looked up on PATH only and never in the current directory (a cloned
repo can ship a `git.exe`), and run from the home directory, not the repo.

The probe table is OS-aware from the start: each entry declares the OSes it applies
to, so adding Linux/macOS probes later only adds entries.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from rtfmup.host.ranges import parse_version

HostOS = Literal["windows", "linux", "macos"]
HostArch = Literal["x86_64", "arm64"]

PROBE_TIMEOUT = 5.0
# A probe that got no answer is retried once with this. A tool's first launch on a cold
# machine (antivirus scanning it) can take far longer than 5 s: on fresh GitHub
# runners every runtime version came back unknown, then all were found warm.
PROBE_RETRY_TIMEOUT = 30.0
_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str


Runner = Callable[[list[str], float], "RunResult | None"]
Which = Callable[[str], "str | None"]


class ToolInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool
    path: str | None = None
    version: str | None = None  # highest, when several are installed
    versions: list[str] = []  # all installed (python, dotnet)
    note: str | None = None


class HostProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    os: HostOS
    arch: HostArch
    tools: dict[str, ToolInfo]
    docker_daemon: bool | None = None  # None: docker not installed
    # T5.2. None where a fact does not apply to this OS or could not be read.
    # Not the analysis schema: a HostProfile is never written into analysis.json.
    distro_id: str | None = None  # Linux: /etc/os-release ID, e.g. "ubuntu"
    distro_like: list[str] = []  # Linux: ID_LIKE, e.g. ["debian"]
    distro_version: str | None = None  # Linux: VERSION_ID
    os_version: str | None = None  # macOS: `sw_vers -productVersion`
    brew_prefix: str | None = None  # macOS: `brew --prefix`, never assumed
    xcode_clt: bool | None = None  # macOS: Command Line Tools installed
    rosetta: bool | None = None  # macOS: this process is translated by Rosetta 2
    is_root: bool | None = None  # POSIX: effective uid is 0 (so no `sudo` is needed)


def normalize_os(raw: str) -> HostOS:
    key = raw.lower()
    if key in ("windows", "win32"):
        return "windows"
    if key in ("linux", "linux2"):
        return "linux"
    if key in ("darwin", "macos"):
        return "macos"
    raise ValueError(f"unsupported OS: {raw}")


def normalize_arch(raw: str) -> HostArch:
    key = raw.lower()
    if key in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if key in ("arm64", "aarch64"):
        return "arm64"
    raise ValueError(f"unsupported architecture: {raw}")


def parse_os_release(text: str) -> dict[str, str]:
    """`KEY=value` lines of /etc/os-release (values may be single or double quoted).
    Format: https://www.freedesktop.org/software/systemd/man/os-release.html"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


OS_RELEASE_PATHS = ("/etc/os-release", "/usr/lib/os-release")  # man os-release order


def read_os_release() -> str | None:
    for path in OS_RELEASE_PATHS:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def find_executable(name: str) -> str | None:
    """PATH lookup that never considers the current directory."""
    cwd = Path.cwd().resolve()
    exts = [""]
    if sys.platform == "win32":
        exts = [
            e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e
        ]
        if Path(name).suffix.lower() in {e.lower() for e in exts}:
            exts = [""]
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry or entry == os.curdir:
            continue
        directory = Path(entry)
        try:
            if directory.resolve() == cwd:
                continue
        except OSError:
            continue
        for ext in exts:
            candidate = directory / (name + ext)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def run_command(argv: list[str], timeout: float) -> RunResult | None:
    """Run a read-only probe. None = could not run or timed out."""
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            cwd=Path.home(),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return RunResult(done.returncode, done.stdout)


# --- the probe table ---------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    run: Runner
    which: Which
    host_os: str = "windows"

    def timed(self, argv: list[str]) -> RunResult | None:
        """Run argv; if it gives no answer in time, once more with a longer limit."""
        res = self.run(argv, PROBE_TIMEOUT)
        return res if res is not None else self.run(argv, PROBE_RETRY_TIMEOUT)

    def query(self, argv: list[str]) -> RunResult | None:
        """Run argv with its executable resolved to an absolute path."""
        exe = self.which(argv[0])
        if exe is None:
            return None
        return self.timed([exe, *argv[1:]])


Probe = Callable[[_Ctx], ToolInfo]


def _sorted_desc(versions: set[str]) -> list[str]:
    return sorted(versions, key=lambda v: parse_version(v) or (0, 0, 0), reverse=True)


def _simple(exe: str, *args: str) -> Probe:
    def probe(ctx: _Ctx) -> ToolInfo:
        path = ctx.which(exe)
        if path is None:
            return ToolInfo(found=False)
        res = ctx.timed([path, *args])
        m = _VERSION.search(res.stdout) if res and res.returncode == 0 else None
        if m is None:
            return ToolInfo(found=True, path=path, note="probe failed or timed out")
        return ToolInfo(found=True, path=path, version=m.group(), versions=[m.group()])

    return probe


def _present(exe: str) -> Probe:
    """Found or not, and never run: for a tool we must not execute at all (`sudo`)."""

    def probe(ctx: _Ctx) -> ToolInfo:
        path = ctx.which(exe)
        return ToolInfo(found=True, path=path) if path else ToolInfo(found=False)

    return probe


def _dotnet(ctx: _Ctx) -> ToolInfo:
    path = ctx.which("dotnet")
    if path is None:
        return ToolInfo(found=False)
    res = ctx.timed([path, "--list-sdks"])
    if res is None or res.returncode != 0:
        return ToolInfo(found=True, path=path, note="probe failed or timed out")
    found = {
        m.group(1)
        for m in re.finditer(r"^(\d+\.\d+\.\d+\S*)\s+\[", res.stdout, re.MULTILINE)
    }
    versions = _sorted_desc(found)
    return ToolInfo(
        found=True,
        path=path,
        version=versions[0] if versions else None,
        versions=versions,
        note=None if versions else "no SDK installed",
    )


def _python(ctx: _Ctx) -> ToolInfo:
    """Installed interpreters: uv-managed and system (full versions), plus the
    Windows `py` launcher (major.minor only, kept when no full version covers it)."""
    full: set[str] = set()
    minor: set[str] = set()
    sources: list[str] = []
    res = ctx.query(
        ["uv", "python", "list", "--only-installed", "--output-format", "json"]
    )
    if res and res.returncode == 0:
        try:
            full |= {str(e["version"]) for e in json.loads(res.stdout)}
            sources.append("uv")
        except (ValueError, KeyError, TypeError):
            pass
    res = ctx.query(["py", "-0p"])
    if res and res.returncode == 0:
        minor |= set(re.findall(r"-V:(\d+\.\d+)", res.stdout))
        sources.append("py")
    if ctx.host_os != "windows":
        # A system interpreter when uv is not installed. Never on Windows, where
        # `python3` is usually the Microsoft Store stub.
        res = ctx.query(["python3", "--version"])
        m = _VERSION.search(res.stdout) if res and res.returncode == 0 else None
        if m:
            full.add(m.group())
            sources.append("python3")
    covered = {".".join(v.split(".")[:2]) for v in full}
    versions = _sorted_desc(full | (minor - covered))
    if not versions:
        return ToolInfo(found=False)
    return ToolInfo(
        found=True,
        path=None,
        version=versions[0],
        versions=versions,
        note="via " + " + ".join(sources),
    )


@dataclass(frozen=True)
class _Entry:
    name: str
    oses: frozenset[str]
    probe: Probe


_ALL = frozenset({"windows", "linux", "macos"})
_WIN = frozenset({"windows"})
_UNIX = frozenset({"linux", "macos"})
_LINUX = frozenset({"linux"})
_MAC = frozenset({"macos"})


def _e(name: str, probe: Probe, oses: frozenset[str] = _ALL) -> _Entry:
    return _Entry(name, oses, probe)


TABLE: tuple[_Entry, ...] = (
    _e("git", _simple("git", "--version")),
    _e("pwsh", _simple("pwsh", "--version")),
    _e("winget", _simple("winget", "--version"), _WIN),
    _e("scoop", _simple("scoop", "--version"), _WIN),
    _e("choco", _simple("choco", "--version"), _WIN),
    _e("uv", _simple("uv", "--version")),
    # T5.2. Flags checked 2026-11-18: `mise --version` (mise.jdx.dev/cli), `brew
    # --version` (docs.brew.sh/Manpage), `apt --version`, `dnf --version`, `pacman
    # --version` and `bash --version` are each the documented version query. On
    # macOS `bash --version` reports the system bash 3.2 that D17 builds for.
    _e("mise", _simple("mise", "--version")),
    _e("brew", _simple("brew", "--version"), _MAC),  # Linuxbrew is out of scope (§8)
    _e("apt", _simple("apt", "--version"), _LINUX),
    _e("dnf", _simple("dnf", "--version"), _LINUX),
    _e("pacman", _simple("pacman", "--version"), _LINUX),
    # The other system package managers a README may name (plan/build.py
    # `_SYSTEM_PMS`): presence only, never run. Without them every unprobed manager
    # tied, and README order chose Gentoo's `emerge` on Ubuntu (T5.8, 2026-11-18).
    _e("apt-get", _present("apt-get"), _LINUX),
    _e("yum", _present("yum"), _LINUX),
    _e("zypper", _present("zypper"), _LINUX),
    _e("emerge", _present("emerge"), _LINUX),
    _e("apk", _present("apk"), _LINUX),
    _e("xbps-install", _present("xbps-install"), _LINUX),
    _e("snap", _present("snap"), _LINUX),
    _e("flatpak", _present("flatpak"), _LINUX),
    _e("port", _present("port"), _MAC),
    _e("nix-env", _present("nix-env"), _UNIX),
    _e("flox", _present("flox"), _UNIX),
    _e("guix", _present("guix"), _LINUX),
    _e("bash", _simple("bash", "--version"), _UNIX),
    _e("sudo", _present("sudo"), _UNIX),  # presence only: never executed (T5.2)
    _e("python", _python),
    _e("node", _simple("node", "--version")),
    _e("npm", _simple("npm", "--version")),
    _e("pnpm", _simple("pnpm", "--version")),
    _e("yarn", _simple("yarn", "--version")),
    _e("bun", _simple("bun", "--version")),
    _e("go", _simple("go", "version")),
    _e("cargo", _simple("cargo", "--version")),
    # `cargo binstall` is the cargo-binstall plugin; `-V` verified against 1.27.0.
    _e("cargo-binstall", _simple("cargo-binstall", "-V")),
    _e("rustc", _simple("rustc", "--version")),
    _e("ruby", _simple("ruby", "--version")),
    _e("php", _simple("php", "--version")),
    _e("composer", _simple("composer", "--version")),
    _e("dotnet", _dotnet),
    _e("docker", _simple("docker", "--version")),
)


def probe_host(
    *,
    os_name: str | None = None,
    arch: str | None = None,
    run: Runner = run_command,
    which: Which = find_executable,
    workers: int = 16,
    read_os_release_text: Callable[[], str | None] = read_os_release,
    euid: int | None = None,
    writable: Callable[[str], bool | None] | None = None,
) -> HostProfile:
    """Probe this host. `run`, `which`, the os-release reader, the uid and the
    write-access check are injectable for tests."""
    host_os = normalize_os(os_name or platform.system())
    host_arch = normalize_arch(arch or platform.machine())
    ctx = _Ctx(run=run, which=which, host_os=host_os)
    entries = [e for e in TABLE if host_os in e.oses]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda e: e.probe(ctx), entries))
    tools = dict(sorted((e.name, r) for e, r in zip(entries, results, strict=True)))

    daemon: bool | None = None
    docker = tools.get("docker")
    if docker is not None and docker.found and docker.path:
        res = run(
            [docker.path, "info", "--format", "{{.ServerVersion}}"], PROBE_TIMEOUT
        )
        daemon = bool(res and res.returncode == 0)
    facts: dict = {}
    if host_os == "linux":
        facts.update(_linux_facts(read_os_release_text()))
    elif host_os == "macos":
        facts.update(_macos_facts(ctx, tools))
        tools = _brew_usable(tools, facts.get("brew_prefix"), writable)
    if host_os != "windows":
        uid = euid if euid is not None else getattr(os, "geteuid", lambda: None)()
        facts["is_root"] = None if uid is None else uid == 0
    return HostProfile(
        os=host_os, arch=host_arch, tools=tools, docker_daemon=daemon, **facts
    )


def _brew_prefix_writable(prefix: str) -> bool | None:
    """Can this user write where brew installs? None when the prefix isn't there."""
    if not os.path.isdir(prefix):
        return None
    dirs = [
        os.path.join(prefix, "Cellar"),
        os.path.join(prefix, "var", "homebrew", "locks"),
    ]
    return all(
        os.access(d, os.W_OK) for d in [d for d in dirs if os.path.isdir(d)] or [prefix]
    )


def _brew_usable(
    tools: dict[str, ToolInfo],
    prefix: str | None,
    writable: Callable[[str], bool | None] | None = None,
) -> dict[str, ToolInfo]:
    """brew counts as missing when this user can't write to its prefix.

    On a Mac with several accounts, one user owns the Homebrew prefix, and brew
    refuses to install for anyone else ("Permission denied" on its lock files, T5.8
    on the Mac mini, 2026-11-18). Homebrew installs into `<prefix>/Cellar` and locks
    under `<prefix>/var/homebrew/locks`; both must be writable. Checked with
    os.access, so no brew command is run to find out.
    """
    brew = tools.get("brew")
    if brew is None or not brew.found or prefix is None:
        return tools
    if (writable or _brew_prefix_writable)(prefix) is not False:
        return tools  # writable, or no prefix on disk to judge by
    blocked = brew.model_copy(
        update={
            "found": False,
            "note": f"installed, but {prefix} is not writable by this user "
            "(Homebrew belongs to another account)",
        }
    )
    return {**tools, "brew": blocked}


def _linux_facts(text: str | None) -> dict:
    if not text:
        return {}
    rel = parse_os_release(text)
    return {
        "distro_id": rel.get("ID") or None,
        "distro_like": rel.get("ID_LIKE", "").split(),
        "distro_version": rel.get("VERSION_ID") or None,
    }


def _macos_facts(ctx: _Ctx, tools: dict[str, ToolInfo]) -> dict:
    """sw_vers, brew --prefix, Command Line Tools and Rosetta, all read-only.

    - `sw_vers -productVersion`: the documented macOS version query (man sw_vers).
    - `brew --prefix`: asked, never assumed (/opt/homebrew on Apple silicon,
      /usr/local on Intel; docs.brew.sh/Manpage, 2026-11-18).
    - `xcode-select -p`: exits 0 and prints the developer directory when the active
      developer tools (the Command Line Tools or Xcode) are installed, non-zero if not.
    - `sysctl -n sysctl.proc_translated`: Apple, "About the Rosetta translation
      environment" (2026-11-18): 1 translated, 0 native, key absent (ENOENT, so
      `sysctl` exits non-zero) on Intel and older systems, meaning native. It
      describes the calling process, which is why an arm64 Mac running an x86_64
      Python reports arch x86_64 and rosetta True.
    """
    facts: dict = {}
    res = ctx.query(["sw_vers", "-productVersion"])
    if res and res.returncode == 0 and res.stdout.strip():
        facts["os_version"] = res.stdout.strip()
    brew = tools.get("brew")
    if brew is not None and brew.found:
        res = ctx.query(["brew", "--prefix"])
        if res and res.returncode == 0 and res.stdout.strip():
            facts["brew_prefix"] = res.stdout.strip().splitlines()[0]
    res = ctx.query(["xcode-select", "-p"])
    if res is not None:
        facts["xcode_clt"] = res.returncode == 0 and bool(res.stdout.strip())
    res = ctx.query(["sysctl", "-n", "sysctl.proc_translated"])
    if res is not None:
        facts["rosetta"] = res.returncode == 0 and res.stdout.strip() == "1"
    return facts
