"""Fixed host profiles for plan tests."""

from __future__ import annotations

from rtfmup.host.probe import HostProfile, ToolInfo


def _t(version: str, *more: str) -> ToolInfo:
    return ToolInfo(
        found=True, path="/bin/x", version=version, versions=[version, *more]
    )


def windows_full() -> HostProfile:
    """Windows 11 x86_64 with every common tool installed at recent versions."""
    tools = {
        "git": _t("2.56.0"),
        "pwsh": _t("7.6.9"),
        "winget": _t("1.29.380"),
        "uv": _t("0.13.7"),
        "python": _t("3.12.11", "3.11.14"),
        "node": _t("26.13.0"),
        "npm": _t("12.2.2"),
        "pnpm": _t("10.20.0"),
        "yarn": _t("4.9.0"),
        "bun": _t("1.3.0"),
        "go": _t("1.27.3"),
        "cargo": _t("1.99.1"),
        "rustc": _t("1.99.1"),
        "ruby": _t("3.4.0"),
        "php": _t("8.4.0"),
        "composer": _t("2.8.0"),
        "dotnet": _t("10.0.114", "9.0.320"),
        "docker": _t("29.9.2"),
    }
    return HostProfile(os="windows", arch="x86_64", tools=tools, docker_daemon=True)


def linux_full() -> HostProfile:
    h = windows_full()
    tools = {k: v for k, v in h.tools.items() if k not in ("winget",)}
    return HostProfile(os="linux", arch="x86_64", tools=tools, docker_daemon=True)


def without(host: HostProfile, *names: str) -> HostProfile:
    """The same host with some tools uninstalled."""
    tools = dict(host.tools)
    for n in names:
        tools[n] = ToolInfo(found=False)
    return host.model_copy(update={"tools": tools})


def with_tool(host: HostProfile, name: str, version: str, *more: str) -> HostProfile:
    return host.model_copy(update={"tools": {**host.tools, name: _t(version, *more)}})


def docker_down(host: HostProfile) -> HostProfile:
    return host.model_copy(update={"docker_daemon": False})


def _bare(os_: str, arch: str, **facts) -> HostProfile:
    """Only git and uv installed: every runtime is missing, so providers decide."""
    tools = {"git": _t("2.56.0"), "uv": _t("0.13.7")}
    return HostProfile(os=os_, arch=arch, tools=tools, docker_daemon=None, **facts)


def with_tools(host: HostProfile, *names: str) -> HostProfile:
    """The same host with these tools present (no version)."""
    extra = {n: ToolInfo(found=True, path=f"/usr/bin/{n}") for n in names}
    return host.model_copy(update={"tools": {**host.tools, **extra}})


def ubuntu(*, root: bool = False, arch: str = "x86_64") -> HostProfile:
    h = _bare(
        "linux",
        arch,
        distro_id="ubuntu",
        distro_like=["debian"],
        distro_version="24.04",
        is_root=root,
    )
    return with_tools(without(h, "uv"), "apt", "sudo")


def fedora(*, root: bool = False) -> HostProfile:
    h = _bare("linux", "x86_64", distro_id="fedora", distro_version="43", is_root=root)
    return with_tools(without(h, "uv"), "dnf", "sudo")


def arch_linux(*, root: bool = False) -> HostProfile:
    h = _bare("linux", "x86_64", distro_id="arch", is_root=root)
    return with_tools(without(h, "uv"), "pacman", "sudo")


def mac(arch: str = "arm64") -> HostProfile:
    h = _bare(
        "macos",
        arch,
        os_version="27.2",
        brew_prefix="/opt/homebrew" if arch == "arm64" else "/usr/local",
        xcode_clt=True,
        rosetta=False,
        is_root=False,
    )
    return without(h, "uv")
