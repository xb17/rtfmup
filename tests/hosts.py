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
