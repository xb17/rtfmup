"""Host probe: all command results are injected; nothing here runs a real tool."""

from __future__ import annotations

import json
import os
import sys

import pytest

from rtfmup.host.probe import (
    HostProfile,
    RunResult,
    find_executable,
    normalize_arch,
    normalize_os,
    probe_host,
)

UV_JSON = json.dumps(
    [
        {"version": "3.12.11", "path": "C:/u/py312/python.exe"},
        {"version": "3.11.14", "path": "C:/u/py311/python.exe"},
    ]
)

OUTPUTS: dict[tuple[str, ...], str] = {
    ("git", "--version"): "git version 2.56.0.windows.4\n",
    ("pwsh", "--version"): "PowerShell 7.6.9\n",
    ("winget", "--version"): "v1.29.380\n",
    ("choco", "--version"): "2.7.5\n",
    ("uv", "--version"): "uv 0.13.7 (316c827e0 2026-11-03 x86_64-pc-windows-msvc)\n",
    ("uv", "python", "list", "--only-installed", "--output-format", "json"): UV_JSON,
    (
        "py",
        "-0p",
    ): " -V:3.12 *  C:\\Python312\\python.exe\n -V:3.9  C:\\Python39\\python.exe\n",
    ("node", "--version"): "v26.13.0\n",
    ("npm", "--version"): "12.2.2\n",
    ("pnpm", "--version"): "10.20.0\n",
    ("go", "version"): "go version go1.27.3 windows/amd64\n",
    ("cargo", "--version"): "cargo 1.99.1 (5f94df478 2026-08-27)\n",
    ("rustc", "--version"): "rustc 1.99.1 (58cf8f9c3 2026-10-13)\n",
    (
        "dotnet",
        "--list-sdks",
    ): "9.0.320 [C:\\Program Files\\dotnet\\sdk]\n10.0.114 [C:\\Program Files\\dotnet\\sdk]\n",
    ("docker", "--version"): "Docker version 29.9.2, build a82e528\n",
}


class Fake:
    def __init__(self, installed: set[str], docker_rc: int = 0, fail=()):
        self.installed = installed
        self.docker_rc = docker_rc
        self.fail = set(fail)
        self.calls: list[tuple[str, ...]] = []

    def which(self, name: str) -> str | None:
        return f"/bin/{name}" if name in self.installed else None

    def run(self, argv: list[str], timeout: float) -> RunResult | None:
        key = (os.path.basename(argv[0]), *argv[1:])  # which() returns /bin/<name>
        self.calls.append(key)
        if key[0] in self.fail:
            return None  # timeout
        if key[:2] == ("docker", "info"):
            return RunResult(self.docker_rc, "29.9.2\n" if self.docker_rc == 0 else "")
        out = OUTPUTS.get(key)
        return RunResult(0, out) if out is not None else RunResult(1, "")


WINDOWS_TOOLS = {
    "git",
    "pwsh",
    "winget",
    "choco",
    "uv",
    "py",
    "node",
    "npm",
    "pnpm",
    "go",
    "cargo",
    "rustc",
    "dotnet",
    "docker",
}


def probe(fake: Fake, os_name: str = "windows") -> HostProfile:
    return probe_host(
        os_name=os_name, arch="x86_64", run=fake.run, which=fake.which, workers=1
    )


def test_versions_parsed_from_real_output_formats():
    p = probe(Fake(WINDOWS_TOOLS))
    v = {n: t.version for n, t in p.tools.items() if t.found}
    assert v["git"] == "2.56.0"
    assert v["pwsh"] == "7.6.9"
    assert v["winget"] == "1.29.380"
    assert v["choco"] == "2.7.5"
    assert v["uv"] == "0.13.7"
    assert v["node"] == "26.13.0"
    assert v["go"] == "1.27.3"
    assert v["cargo"] == "1.99.1"
    assert v["rustc"] == "1.99.1"
    assert v["docker"] == "29.9.2"


def test_not_installed_means_found_false_and_no_command_run():
    fake = Fake({"git"})
    p = probe(fake)
    assert p.tools["git"].found is True
    assert p.tools["node"].found is False
    assert p.tools["node"].version is None
    assert ("node", "--version") not in fake.calls


def test_python_versions_merge_uv_and_launcher_highest_first():
    p = probe(Fake(WINDOWS_TOOLS))
    py = p.tools["python"]
    assert py.found
    # uv gives full versions, `py -0p` gives major.minor (3.9 is only known there)
    assert py.versions == ["3.12.11", "3.11.14", "3.9"]
    assert py.version == "3.12.11"


def test_python_not_found_without_uv_or_py():
    p = probe(Fake({"git"}))
    assert p.tools["python"].found is False
    assert p.tools["python"].versions == []


def test_dotnet_lists_every_sdk():
    p = probe(Fake(WINDOWS_TOOLS))
    d = p.tools["dotnet"]
    assert d.versions == ["10.0.114", "9.0.320"]
    assert d.version == "10.0.114"


def test_timeout_keeps_found_but_unknown_version():
    p = probe(Fake(WINDOWS_TOOLS, fail={"node"}))
    node = p.tools["node"]
    assert node.found is True
    assert node.version is None
    assert node.note == "probe failed or timed out"


@pytest.mark.parametrize(
    ("rc", "expected"), [(0, True), (1, False)], ids=["up", "down"]
)
def test_docker_daemon_reachability(rc, expected):
    p = probe(Fake(WINDOWS_TOOLS, docker_rc=rc))
    assert p.docker_daemon is expected


def test_docker_daemon_unknown_when_docker_missing():
    fake = Fake({"git"})
    p = probe(fake)
    assert p.docker_daemon is None
    assert not any(c[:2] == ("docker", "info") for c in fake.calls)


def test_probe_table_is_os_aware():
    on_windows = probe(Fake(WINDOWS_TOOLS), "windows")
    on_linux = probe(Fake(WINDOWS_TOOLS), "linux")
    assert {"winget", "scoop", "choco"} <= set(on_windows.tools)
    assert not {"winget", "scoop", "choco"} & set(on_linux.tools)
    assert "git" in on_linux.tools


def test_profile_is_deterministic_and_sorted():
    a = probe(Fake(WINDOWS_TOOLS)).model_dump_json()
    b = probe(Fake(WINDOWS_TOOLS)).model_dump_json()
    assert a == b
    assert list(probe(Fake(WINDOWS_TOOLS)).tools) == sorted(
        probe(Fake(WINDOWS_TOOLS)).tools
    )


def test_probes_only_run_read_only_version_style_commands():
    fake = Fake(WINDOWS_TOOLS)
    probe(fake)
    allowed_flags = {"--version", "version", "--list-sdks", "-0p", "-V"}
    for argv in fake.calls:
        if argv[:3] == ("uv", "python", "list"):
            continue
        if argv[:2] == ("docker", "info"):
            continue
        assert allowed_flags & set(argv), argv


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AMD64", "x86_64"),
        ("x86_64", "x86_64"),
        ("arm64", "arm64"),
        ("aarch64", "arm64"),
    ],
)
def test_normalize_arch(raw, expected):
    assert normalize_arch(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Windows", "windows"),
        ("win32", "windows"),
        ("Linux", "linux"),
        ("Darwin", "macos"),
    ],
)
def test_normalize_os(raw, expected):
    assert normalize_os(raw) == expected


def test_normalize_rejects_unknown_platforms():
    with pytest.raises(ValueError):
        normalize_os("FreeBSD")
    with pytest.raises(ValueError):
        normalize_arch("riscv64")


def test_find_executable_never_picks_up_the_current_directory(tmp_path, monkeypatch):
    # An untrusted repo can ship `git.exe`/`git` in its root. Windows would run it
    # first for a bare name; we must not.
    name = "rtfmup_planted_tool"
    planted = tmp_path / "repo" / (name + (".exe" if sys.platform == "win32" else ""))
    planted.parent.mkdir()
    planted.write_text("x")
    planted.chmod(0o755)
    safe = tmp_path / "bin"
    safe.mkdir()
    monkeypatch.chdir(planted.parent)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "", str(safe)]))
    assert find_executable(name) is None
    real = safe / planted.name
    real.write_text("x")
    real.chmod(0o755)
    found = find_executable(name)
    assert found is not None
    assert os.path.normcase(found) == os.path.normcase(str(real))
