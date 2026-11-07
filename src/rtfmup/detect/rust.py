"""Rust manifests: Cargo.toml, Cargo.lock, rust-toolchain(.toml)."""

from __future__ import annotations

from rtfmup.detect._common import Detection, find_key_line


def detect(d: Detection) -> None:
    text = d.read("Cargo.toml")
    if text is not None:
        d.manifest("Cargo.toml", "cargo_toml", "rust.cargo_toml")
        lock = "Cargo.lock" if d.exists("Cargo.lock") else None
        d.package_manager("cargo", lock, lock or "Cargo.toml", 1, "rust.cargo")
        data = d.toml("Cargo.toml", text)
        package = data.get("package") if data else None
        if isinstance(package, dict) and isinstance(package.get("rust-version"), str):
            line = find_key_line(text, "rust-version")
            d.runtime(
                "rust", package["rust-version"], "Cargo.toml", line, "rust.rust_version"
            )

    _toolchain(d)


def _toolchain(d: Detection) -> None:
    for name in ("rust-toolchain.toml", "rust-toolchain"):
        text = d.read(name)
        if text is None:
            continue
        d.manifest(name, "rust_toolchain", "rust.toolchain")
        channel: str | None = None
        line: int | None = None
        if "[toolchain]" in text:
            data = d.toml(name, text)
            tc = data.get("toolchain") if data else None
            if isinstance(tc, dict) and isinstance(tc.get("channel"), str):
                channel = tc["channel"]
                line = find_key_line(text, "[toolchain]", "channel")
        else:  # legacy format: the channel alone on one line
            for i, raw in enumerate(text.splitlines(), start=1):
                if raw.strip() and not raw.strip().startswith("#"):
                    channel, line = raw.strip(), i
                    break
        if channel:
            d.runtime("rust", channel, name, line, "rust.toolchain")
        return
