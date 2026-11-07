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


def normalize_os(raw: str) -> HostOS:
    key = raw.lower()
    if key in ("windows", "win32"):
        return "windows"
    if key in ("linux", "linux2"):
        return "linux"
    if key == "darwin":
        return "macos"
    raise ValueError(f"unsupported OS: {raw}")


def normalize_arch(raw: str) -> HostArch:
    key = raw.lower()
    if key in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if key in ("arm64", "aarch64"):
        return "arm64"
    raise ValueError(f"unsupported architecture: {raw}")


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

    def query(self, argv: list[str]) -> RunResult | None:
        """Run argv with its executable resolved to an absolute path."""
        exe = self.which(argv[0])
        if exe is None:
            return None
        return self.run([exe, *argv[1:]], PROBE_TIMEOUT)


Probe = Callable[[_Ctx], ToolInfo]


def _sorted_desc(versions: set[str]) -> list[str]:
    return sorted(versions, key=lambda v: parse_version(v) or (0, 0, 0), reverse=True)


def _simple(exe: str, *args: str) -> Probe:
    def probe(ctx: _Ctx) -> ToolInfo:
        path = ctx.which(exe)
        if path is None:
            return ToolInfo(found=False)
        res = ctx.run([path, *args], PROBE_TIMEOUT)
        m = _VERSION.search(res.stdout) if res and res.returncode == 0 else None
        if m is None:
            return ToolInfo(found=True, path=path, note="probe failed or timed out")
        return ToolInfo(found=True, path=path, version=m.group(), versions=[m.group()])

    return probe


def _dotnet(ctx: _Ctx) -> ToolInfo:
    path = ctx.which("dotnet")
    if path is None:
        return ToolInfo(found=False)
    res = ctx.run([path, "--list-sdks"], PROBE_TIMEOUT)
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


def _e(name: str, probe: Probe, oses: frozenset[str] = _ALL) -> _Entry:
    return _Entry(name, oses, probe)


TABLE: tuple[_Entry, ...] = (
    _e("git", _simple("git", "--version")),
    _e("pwsh", _simple("pwsh", "--version")),
    _e("winget", _simple("winget", "--version"), _WIN),
    _e("scoop", _simple("scoop", "--version"), _WIN),
    _e("choco", _simple("choco", "--version"), _WIN),
    _e("uv", _simple("uv", "--version")),
    _e("python", _python),
    _e("node", _simple("node", "--version")),
    _e("npm", _simple("npm", "--version")),
    _e("pnpm", _simple("pnpm", "--version")),
    _e("yarn", _simple("yarn", "--version")),
    _e("bun", _simple("bun", "--version")),
    _e("go", _simple("go", "version")),
    _e("cargo", _simple("cargo", "--version")),
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
) -> HostProfile:
    """Probe this host. `run` and `which` are injectable for tests."""
    host_os = normalize_os(os_name or platform.system())
    host_arch = normalize_arch(arch or platform.machine())
    ctx = _Ctx(run=run, which=which)
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
    return HostProfile(os=host_os, arch=host_arch, tools=tools, docker_daemon=daemon)
