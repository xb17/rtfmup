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
    ("cargo-binstall", "-V"): "1.27.0\n",
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
    "cargo-binstall",
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


def test_cargo_binstall_is_probed_with_dash_v():
    p = probe(Fake(WINDOWS_TOOLS))
    assert p.tools["cargo-binstall"].version == "1.27.0"
    assert probe(Fake({"cargo"})).tools["cargo-binstall"].found is False


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


class SlowFirstLaunch(Fake):
    """A cold machine: each tool's first launch outlasts the short timeout (AV scan)."""

    def __init__(self, installed: set[str]):
        super().__init__(installed)
        self.timeouts: list[float] = []
        self._seen: set[str] = set()

    def run(self, argv, timeout):
        name = os.path.basename(argv[0])
        self.timeouts.append(timeout)
        if name not in self._seen:
            self._seen.add(name)
            return None
        return super().run(argv, timeout)


def test_a_probe_that_times_out_is_retried_once_with_a_longer_limit():
    """Seen on fresh GitHub runners: every version was `?` cold, all found warm."""
    fake = SlowFirstLaunch(WINDOWS_TOOLS)
    p = probe(fake)
    assert p.tools["node"].version == "26.13.0"
    assert p.tools["go"].version == "1.27.3"
    assert p.tools["dotnet"].versions == ["10.0.114", "9.0.320"]
    assert max(fake.timeouts) > min(fake.timeouts)


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


# --- T5.2: Linux and macOS facts ----------------------------------------------------

UBUNTU = """\
PRETTY_NAME="Ubuntu 24.04.3 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
ID=ubuntu
ID_LIKE=debian
"""
FEDORA = "NAME=Fedora\nID=fedora\nVERSION_ID=43\n"
ARCH = "NAME='Arch Linux'\nID=arch\nBUILD_ID=rolling\n# no VERSION_ID on Arch\n"
UNIX_OUTPUTS: dict[tuple[str, ...], str] = {
    **OUTPUTS,
    ("mise", "--version"): "2026.11.3 linux-x64 (2026-11-10)\n",
    ("apt", "--version"): "apt 2.8.3 (amd64)\n",
    ("dnf", "--version"): "4.21.0\n  Installed: dnf-0:4.21.0-1.fc43.noarch at x\n",
    ("pacman", "--version"): (
        "\n .--.                  Pacman v7.0.0 - libalpm v15.0.0\n"
        "/ _.-' .-.  .-.  .-.   Copyright (C) 2006-2024 Pacman Development Team\n"
    ),
    (
        "bash",
        "--version",
    ): "GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu)\n",
    ("python3", "--version"): "Python 3.12.3\n",
    ("brew", "--version"): "Homebrew 5.0.1\n",
    ("brew", "--prefix"): "/opt/homebrew\n",
    ("sw_vers", "-productVersion"): "27.2\n",
    ("xcode-select", "-p"): "/Library/Developer/CommandLineTools\n",
    ("sysctl", "-n", "sysctl.proc_translated"): "0\n",
}


class UnixFake(Fake):
    def __init__(self, installed, outputs=None, rc=None):
        super().__init__(installed)
        self.outputs = outputs or UNIX_OUTPUTS
        self.rc = rc or {}

    def run(self, argv, timeout):
        key = (os.path.basename(argv[0]), *argv[1:])
        self.calls.append(key)
        if key[0] in self.rc:
            return RunResult(self.rc[key[0]], "")
        out = self.outputs.get(key)
        return RunResult(0, out) if out is not None else RunResult(1, "")


def probe_unix(fake, os_name, arch="x86_64", release=None, euid=1000):
    return probe_host(
        os_name=os_name,
        arch=arch,
        run=fake.run,
        which=fake.which,
        workers=1,
        read_os_release_text=lambda: release,
        euid=euid,
    )


@pytest.mark.parametrize(
    ("release", "distro", "like", "version"),
    [
        (UBUNTU, "ubuntu", ["debian"], "24.04"),
        (FEDORA, "fedora", [], "43"),
        (ARCH, "arch", [], None),
        (None, None, [], None),
    ],
    ids=["ubuntu", "fedora", "arch", "no-os-release"],
)
def test_linux_distro_from_os_release(release, distro, like, version):
    p = probe_unix(UnixFake({"git", "apt", "dnf", "pacman"}), "linux", release=release)
    assert (p.distro_id, p.distro_like, p.distro_version) == (distro, like, version)
    assert p.brew_prefix is None and p.xcode_clt is None and p.rosetta is None
    assert p.os_version is None


def test_linux_probes_the_distro_managers_and_bash_but_not_brew():
    fake = UnixFake({"git", "apt", "dnf", "pacman", "mise", "bash", "brew"})
    p = probe_unix(fake, "linux", release=UBUNTU)
    v = {n: t.version for n, t in p.tools.items() if t.found}
    assert v["apt"] == "2.8.3" and v["dnf"] == "4.21.0" and v["pacman"] == "7.0.0"
    assert v["mise"] == "2026.11.3" and v["bash"] == "5.2.21"
    assert "brew" not in p.tools
    assert not any(c[0] in ("sw_vers", "xcode-select", "sysctl") for c in fake.calls)


def test_macos_arm64_facts():
    fake = UnixFake(
        {"git", "brew", "bash", "mise", "uv", "sw_vers", "xcode-select", "sysctl"}
    )
    p = probe_unix(fake, "macos", arch="arm64", release=UBUNTU)
    assert (p.os, p.arch) == ("macos", "arm64")
    assert p.os_version == "27.2"
    assert p.brew_prefix == "/opt/homebrew"  # whatever `brew --prefix` said
    assert p.xcode_clt is True and p.rosetta is False
    assert p.distro_id is None  # os-release is a Linux fact
    assert p.tools["brew"].version == "5.0.1"
    assert not {"apt", "dnf", "pacman"} & set(p.tools)


def test_macos_x86_64_has_no_translation_key_and_a_different_prefix():
    outputs = {k: v for k, v in UNIX_OUTPUTS.items() if k[0] != "sysctl"}
    outputs[("brew", "--prefix")] = "/usr/local\n"
    outputs[("sw_vers", "-productVersion")] = "26.1\n"
    fake = UnixFake({"git", "brew", "sysctl", "sw_vers"}, outputs)
    p = probe_unix(fake, "macos", arch="x86_64")
    assert p.brew_prefix == "/usr/local" and p.os_version == "26.1"
    assert p.rosetta is False  # `sysctl` exits non-zero when the key is absent


def test_macos_under_rosetta_and_without_tools():
    outputs = dict(UNIX_OUTPUTS)
    outputs[("sysctl", "-n", "sysctl.proc_translated")] = "1\n"
    fake = UnixFake({"git", "sysctl"}, outputs)
    p = probe_unix(fake, "macos", arch="x86_64")
    assert p.rosetta is True
    assert p.brew_prefix is None and not p.tools["brew"].found
    assert p.xcode_clt is None  # xcode-select is not installed here: unknown
    fake = UnixFake({"git", "xcode-select"}, rc={"xcode-select": 2})
    assert probe_unix(fake, "macos").xcode_clt is False


def test_is_root_follows_the_effective_uid_and_sudo_is_never_run():
    fake = UnixFake({"git", "sudo", "apt"})
    root = probe_unix(fake, "linux", release=UBUNTU, euid=0)
    user = probe_unix(fake, "linux", release=UBUNTU, euid=1000)
    assert root.is_root is True and user.is_root is False
    assert root.tools["sudo"].found and root.tools["sudo"].path
    assert not any(c[0] == "sudo" for c in fake.calls)  # presence only
    assert probe(Fake(WINDOWS_TOOLS)).is_root is None  # Windows: not a uid question


def test_mise_is_probed_on_every_os():
    for os_name in ("windows", "linux", "macos"):
        p = probe_unix(UnixFake({"mise"}), os_name, release=UBUNTU)
        assert p.tools["mise"].version == "2026.11.3"


def test_parse_os_release_handles_quotes_comments_and_junk():
    from rtfmup.host.probe import parse_os_release

    rel = parse_os_release("# c\nID=\"x y\"\nNAME='N'\nBAD LINE\n\nEMPTY=\n")
    assert rel == {"ID": "x y", "NAME": "N", "EMPTY": ""}


def test_doctor_text_shows_the_new_facts():
    import io

    from rich.console import Console

    from rtfmup import ui

    p = probe_unix(
        UnixFake({"git", "brew", "sw_vers", "xcode-select", "sysctl"}),
        "macos",
        arch="arm64",
    )
    buf = io.StringIO()
    ui.render_host(p, Console(file=buf, width=120))
    text = buf.getvalue()
    assert "macOS: 27.2" in text and "Homebrew prefix: /opt/homebrew" in text
    assert "Command Line Tools: yes" in text and "Rosetta translation: no" in text
    p = probe_unix(UnixFake({"git"}), "linux", release=UBUNTU, euid=0)
    buf = io.StringIO()
    ui.render_host(p, Console(file=buf, width=120))
    assert "Distro: ubuntu 24.04 (like debian)" in buf.getvalue()
    assert "Running as root: yes" in buf.getvalue()


def test_unix_finds_a_system_python3_without_uv_but_windows_never_asks():
    p = probe_unix(UnixFake({"python3"}), "linux", release=UBUNTU)
    assert p.tools["python"].versions == ["3.12.3"]
    assert "python3" in (p.tools["python"].note or "")
    fake = UnixFake({"python3"})
    probe_unix(fake, "windows")
    assert ("python3", "--version") not in fake.calls


# --- T5.8 bench findings ---------------------------------------------------------------


def test_linux_probes_every_system_manager_a_readme_may_name_without_running_them():
    """`apt-get` and `emerge` were unprobed, so they tied and README order chose Gentoo."""
    fake = UnixFake({"git", "apt", "apt-get"})
    p = probe_unix(fake, "linux", release=UBUNTU)
    assert p.tools["apt-get"].found and not p.tools["emerge"].found
    assert {"yum", "zypper", "apk", "xbps-install", "nix-env", "guix"} <= set(p.tools)
    assert not any(c[0] in ("apt-get", "emerge") for c in fake.calls)  # presence only


def test_brew_counts_as_missing_when_its_prefix_belongs_to_another_user():
    """The Mac mini: brew is on PATH, but gitea-runner can't write /opt/homebrew."""
    fake = UnixFake({"git", "brew", "sw_vers"})
    p = probe_host(
        os_name="macos",
        arch="arm64",
        run=fake.run,
        which=fake.which,
        workers=1,
        euid=1000,
        writable=lambda prefix: False,
    )
    assert p.brew_prefix == "/opt/homebrew"
    assert not p.tools["brew"].found
    assert "not writable" in (p.tools["brew"].note or "")
    ok = probe_host(
        os_name="macos",
        arch="arm64",
        run=fake.run,
        which=fake.which,
        workers=1,
        euid=1000,
        writable=lambda prefix: True,
    )
    assert ok.tools["brew"].found
    # no prefix on disk to judge by: brew stays as found
    unknown = probe_host(
        os_name="macos",
        arch="arm64",
        run=fake.run,
        which=fake.which,
        workers=1,
        euid=1000,
        writable=lambda prefix: None,
    )
    assert unknown.tools["brew"].found
