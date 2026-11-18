"""T4.0: README install alternatives are alternatives, not steps in sequence.

A README install section often offers several ways to get the project itself:
`winget install ...`, `cargo install ...`, `cargo binstall ...`, a source build, a
release download. We find the commands that deliver the repo's own project
("self-delivering"), give each fence a *route*, choose one route and drop the fences of
the others. Nothing is dropped silently: every dropped command is returned as a
`SkippedAlternative` with the reason.

Pure planning: reads the README and a few manifests, executes nothing, and changes no
analysis data.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from rtfmup.host.probe import HostProfile
from rtfmup.models import Analysis, Command, Component
from rtfmup.plan.model import Gap, SkippedAlternative
from rtfmup.plan.risk import rate
from rtfmup.readme.parse import Section, parse
from rtfmup.readme.rst import parse_rst

_RISK = {"low": 0, "medium": 1, "high": 2}
_SPECIFICITY = {"any": 0, "unix": 1, "linux": 2, "macos": 2, "windows": 2}


class Cand(Protocol):
    """What this module needs from the builder's candidate commands."""

    cmd: Command
    text: str


@dataclass(frozen=True)
class Route:
    name: str  # system pm name, "cargo install", "source", "download", ...
    tool: str | None  # HostProfile.tools key, None when there is nothing to probe


@dataclass
class Resolution:
    drop: set[str] = field(default_factory=set)  # Command.id values to leave out
    skipped: list[SkippedAlternative] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


# --- who is "us": names the project goes by -------------------------------------------


@dataclass
class _Own:
    names: set[str] = field(default_factory=set)  # normalized
    go_paths: set[str] = field(default_factory=set)  # module path, owner/repo
    slugs: set[str] = field(default_factory=set)  # owner/repo of the own repo


def _norm(s: str) -> str:
    """PEP 503 normalization (also fine for crates, npm and winget names)."""
    return re.sub(r"[-_.]+", "-", s.strip().lower())


def _load(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8-sig")
        return tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _own(a: Analysis, comp: Component, own_urls: Sequence[str]) -> _Own:
    from rtfmup.plan.build import repo_key

    root = Path(a.root)
    own = _Own()

    def add(name: object) -> None:
        if isinstance(name, str) and name.strip():
            own.names.add(_norm(name.rsplit("/", 1)[-1]))

    for base in dict.fromkeys([root / comp.path, root]):
        cargo = _load(base / "Cargo.toml")
        if isinstance(cargo, dict):
            add((cargo.get("package") or {}).get("name"))
            for b in cargo.get("bin") or []:
                add(b.get("name") if isinstance(b, dict) else None)
        py = _load(base / "pyproject.toml")
        if isinstance(py, dict):
            add((py.get("project") or {}).get("name"))
        pkg = _load(base / "package.json")
        if isinstance(pkg, dict):
            add(pkg.get("name"))
        try:
            gomod = (base / "go.mod").read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            gomod = ""
        if m := re.search(r"(?m)^module\s+(\S+)", gomod):
            mod = m.group(1).strip('"')
            own.go_paths.add(mod.lower())
            add(posixpath.basename(mod))
    for url in own_urls:
        key = repo_key(url)
        parts = key.split("/")
        add(parts[-1])
        if len(parts) >= 3:
            own.slugs.add("/".join(parts[-2:]))
            own.go_paths.add(key)
    if not own_urls:
        add(root.name)
    own.names.discard("")
    return own


def _is_own(token: str, own: _Own) -> bool:
    """Does a package argument name this project?

    A winget ID matches when one of its parts after the publisher starts with a name
    (`BurntSushi.ripgrep.MSVC`; the plan's "last part" wording does not fit its own
    example). Same for `nixpkgs.ripgrep`.
    """
    t = token.strip("'\"")
    t = t.removeprefix("@")  # npm scope
    t = re.split(r"[@=<>~!\[;]", t, maxsplit=1)[0]
    t = t.rsplit("/", 1)[-1]
    if not t:
        return False
    if _norm(t) in own.names:
        return True
    parts = t.split(".")
    return len(parts) > 1 and any(
        len(n) >= 3 and _norm(p).startswith(n) for p in parts[1:] for n in own.names
    )


# --- classifying one command ----------------------------------------------------------

_VERBS = {"install", "add", "i"}
_ALWAYS_INSTALL = {"xbps-install", "pkg_add", "emerge"}
_CARGO_VALUE_FLAGS = {
    "--version", "--vers", "--root", "--features", "-F", "--target", "--profile",
    "--jobs", "-j", "--git", "--branch", "--tag", "--rev", "--registry", "--index",
    "--bin", "--example", "--path", "--targets", "--install-path", "--manifest-path",
    "--strategies", "--pkg-url", "--bin-dir", "--pkg-fmt", "--target-dir",
}  # fmt: skip
_PIP_VALUE_FLAGS = {
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url", "-t",
    "--target", "--extra-index-url", "-f", "--find-links", "--prefix", "--root",
}  # fmt: skip
_DOWNLOADERS = {"curl", "wget", "iwr", "invoke-webrequest", "irm", "invoke-restmethod"}
_RELEASE_URL = re.compile(
    r"github\.com/([\w.-]+)/([\w.-]+)/releases/download/", re.IGNORECASE
)


def _tokens(text: str) -> list[str]:
    try:
        toks = shlex.split(text, posix=True)
    except ValueError:
        toks = text.split()
    while toks and (
        toks[0].lower() in ("sudo", "doas", "env")
        or ("=" in toks[0] and not toks[0].startswith("-"))
    ):
        toks = toks[1:]
    return toks


def _positionals(args: Sequence[str], value_flags: set[str] = frozenset()) -> list[str]:  # type: ignore[assignment]
    out: list[str] = []
    it = iter(args)
    for a in it:
        if a.startswith("-"):
            if a in value_flags:
                next(it, None)
            continue
        out.append(a)
    return out


def _system_install(pm: str, args: list[str], own: _Own) -> bool:
    pos = _positionals(args)
    verb = pos[0] if pos and pos[0] in _VERBS else None
    flagged = any(a.startswith(("-S", "-i")) or a == "--install" for a in args)
    if not (verb or flagged or pm in _ALWAYS_INSTALL):
        return False
    return any(_is_own(p, own) for p in (pos[1:] if verb else pos))


def classify(
    text: str, own: _Own, system_pm: Callable[[str], str | None]
) -> Route | None:
    """The route this command delivers the project by, or None if it does not."""
    toks = _tokens(text)
    if not toks:
        return None
    exe = re.sub(r"\.exe$", "", toks[0].lower())
    args = toks[1:]

    if exe in _DOWNLOADERS:
        m = _RELEASE_URL.search(text)
        if m and (
            f"{m.group(1)}/{m.group(2)}".lower() in own.slugs
            or _norm(m.group(2)) in own.names
        ):
            return Route("download", None)
        return None

    if (pm := system_pm(text)) is not None:
        return Route(pm, pm) if _system_install(pm, args, own) else None

    if exe == "cargo" and args:
        sub, rest = args[0], args[1:]
        if sub == "build":
            return Route("source", "cargo")
        if sub in ("install", "binstall"):
            if sub == "install" and any(
                r == "--path" or r.startswith("--path=") for r in rest
            ):
                return Route("source", "cargo")
            hit = any(_is_own(p, own) for p in _positionals(rest, _CARGO_VALUE_FLAGS))
            if hit:
                if sub == "install":
                    return Route("cargo install", "cargo")
                return Route("cargo binstall", "cargo-binstall")
        return None

    if exe == "go" and args:
        sub, rest = args[0], _positionals(args[1:])
        if sub == "build":
            return Route("source", "go")
        if sub == "install":
            if any(p.startswith(".") for p in rest):
                return Route("source", "go")
            for p in rest:
                path = p.split("@", 1)[0].lower()
                if any(path.startswith(g) for g in own.go_paths) or _is_own(
                    posixpath.basename(path), own
                ):
                    return Route("go install", "go")
        return None

    pip_args: list[str] | None = None
    tool = "python"
    if exe in ("pip", "pip3"):
        pip_args = args
    elif exe in ("python", "python3", "py") and args[:2] == ["-m", "pip"]:
        pip_args = args[2:]
    elif exe == "uv" and args[:2] == ["pip", "install"]:
        pip_args, tool = args[1:], "uv"
    if pip_args is not None:
        if not pip_args or pip_args[0] != "install":
            return None
        rest = pip_args[1:]
        if any(r in ("-r", "--requirement") for r in rest):
            return None
        pos = _positionals(rest, _PIP_VALUE_FLAGS)
        if any(r in ("-e", "--editable") for r in rest) or any(
            p.startswith(".") for p in pos
        ):
            return Route("source", tool)
        return Route("pip", tool) if any(_is_own(p, own) for p in pos) else None

    if exe == "pipx" and args[:1] == ["install"]:
        hit = any(_is_own(p, own) for p in _positionals(args[1:]))
        return Route("pipx", "pipx") if hit else None
    if exe == "uv" and args[:2] == ["tool", "install"]:
        hit = any(_is_own(p, own) for p in _positionals(args[2:]))
        return Route("pipx", "uv") if hit else None
    if exe == "uv" and args[:1] == ["sync"]:
        return Route("source", "uv")

    if exe in ("npm", "pnpm", "yarn", "bun"):
        if exe == "npm" and args[:2] == ["run", "build"]:
            return Route("source", "npm")
        is_global = any(a in ("-g", "--global") for a in args) or args[:1] == ["global"]
        if is_global and any(
            _is_own(p, own)
            for p in _positionals(args)
            if p not in ("install", "i", "add", "global")
        ):
            return Route(f"{exe} -g", exe)
    return None


# --- fences ----------------------------------------------------------------------------


@dataclass
class _Fence:
    key: tuple
    order: int
    cands: list[Any] = field(default_factory=list)
    route: Route | None = None
    route_cand: Any = None
    platform: str = "any"


def _fence_ranges(
    a: Analysis, files: set[str]
) -> dict[str, list[tuple[int, int]]] | None:
    """README file -> [(first line, last line)] of every code block; None if a file
    cannot be re-read."""
    root = Path(a.root)
    out: dict[str, list[tuple[int, int]]] = {}
    for rel in files:
        try:
            text = (root / rel).read_text(encoding="utf-8-sig")
            tree: Section = (
                parse_rst(text) if rel.lower().endswith(".rst") else parse(text)
            )
        except (OSError, UnicodeDecodeError, ValueError):
            return None
        out[rel] = sorted(
            (blk.line_start, blk.line_end)
            for _, blk in tree.walk_blocks()
            if blk.kind in ("fence", "code_block")
        )
    return out


def _fence_key(cmd: Command, ranges: dict[str, list[tuple[int, int]]] | None) -> tuple:
    ev = cmd.evidence[0]
    if ranges is not None and ev.line_start is not None:
        # innermost block: the smallest range that contains the line
        hits = [r for r in ranges.get(ev.file, []) if r[0] <= ev.line_start <= r[1]]
        if hits:
            return (ev.file, *min(hits, key=lambda r: r[1] - r[0]))
    return ("cmd", cmd.id)


# --- ranking ----------------------------------------------------------------------------


@dataclass
class _RouteInfo:
    route: Route
    fences: list[_Fence]
    fits: bool
    presence: int  # 0 found, 1 not probed, 2 missing, 3 missing package manager
    risk: int
    order: int

    def rank(self) -> tuple[int, int, int, int]:
        return (0 if self.fits else 1, self.presence, self.risk, self.order)


_PRESENCE_WORDS = ("tool found", "tool not probed", "tool missing", "tool missing")
_RISK_WORDS = ("low", "medium", "high")


def _info(
    route: Route, fences: list[_Fence], host: HostProfile, platform_ok: Callable
) -> _RouteInfo:
    tool = host.tools.get(route.tool) if route.tool else None
    if tool is not None:
        presence = 0 if tool.found else 2
    elif route.tool == "cargo-binstall":
        presence = 2  # a cargo plugin counts as missing unless the probe finds it
    else:
        presence = 1  # not probed
    if presence == 2 and route.tool == route.name:
        # A missing system package manager (winget, scoop, apt...) is worse than a
        # missing language tool: rtfmup can provision cargo or go, but it never
        # installs a package manager (D12).
        presence = 3
    risk = 0
    for f in fences:
        for c in f.cands:
            level, _ = rate(c.text, flags=c.cmd.flags, needs_shell=c.cmd.needs_shell)
            risk = max(risk, _RISK[level])
    return _RouteInfo(
        route=route,
        fences=fences,
        fits=all(platform_ok(f.platform, host.os) for f in fences),
        presence=presence,
        risk=risk,
        order=min(f.order for f in fences),
    )


def _variant_key(text: str) -> tuple[str, ...]:
    lead: list[str] = []
    for t in _tokens(text):
        if t.startswith("-"):
            break
        lead.append(t)
    return tuple(lead)


def _skip(c: Any, reason: str) -> SkippedAlternative:
    return SkippedAlternative(text=c.text, evidence=c.cmd.evidence[:1], reason=reason)


# --- the entry point --------------------------------------------------------------------


def resolve_alternatives(
    a: Analysis,
    comp: Component,
    cands: Sequence[Cand],
    host: HostProfile,
    own_urls: Sequence[str] = (),
    *,
    system_dropped: set[str] = frozenset(),  # type: ignore[assignment]
) -> Resolution:
    """Choose one install route and one variant per tool and subcommand.

    `cands` are the plan's README command candidates in README order.
    `system_dropped` holds the ids `_one_system_manager` already left out: their whole
    fences go too, so a leftover `sudo subscription-manager ...` does not stay behind.
    """
    from rtfmup.plan.build import _platform_ok, _system_pm

    res = Resolution()
    own = _own(a, comp, own_urls)

    files = {c.cmd.evidence[0].file for c in cands}
    ranges = _fence_ranges(a, files)
    if ranges is None:
        res.gaps.append(
            Gap(
                kind="unresolved",
                message=(
                    "Could not re-read the README to group commands into fenced "
                    "blocks; each command is treated as its own install alternative."
                ),
            )
        )

    fences: dict[tuple, _Fence] = {}
    for n, c in enumerate(cands):
        f = fences.setdefault(_fence_key(c.cmd, ranges), _Fence(("",), n))
        f.cands.append(c)
    for key, f in fences.items():
        f.key = key
        f.platform = max(
            (c.cmd.platform for c in f.cands), key=lambda p: _SPECIFICITY.get(p, 0)
        )
        for c in f.cands:
            if (route := classify(c.text, own, _system_pm)) is not None:
                f.route, f.route_cand = route, c
                break

    def drop_fence(
        f: _Fence, reason: str, *, mate_reason: str | None = None, lead: Any = None
    ) -> None:
        for c in f.cands:
            if c.cmd.id not in res.drop:
                res.drop.add(c.cmd.id)
                why = reason if lead is None or c is lead else mate_reason
                res.skipped.append(_skip(c, why or reason))

    # fences whose system-manager commands were already dropped go with them
    live: list[_Fence] = []
    for f in fences.values():
        if any(c.cmd.id in system_dropped for c in f.cands):
            drop_fence(f, "another system package manager was chosen for this section")
        else:
            live.append(f)

    routes: dict[str, list[_Fence]] = {}
    for f in live:
        if f.route is not None:
            routes.setdefault(f.route.name, []).append(f)

    infos = {
        name: _info(fs[0].route, fs, host, _platform_ok)  # type: ignore[arg-type]
        for name, fs in routes.items()
    }
    if len(infos) > 1:
        best = min(infos.values(), key=_RouteInfo.rank)
        if best.fits:
            why = ", ".join(
                (_PRESENCE_WORDS[best.presence], f"{_RISK_WORDS[best.risk]} risk")
            )
            chosen = best.fences[0].route_cand.text
            for name, info in infos.items():
                if name == best.route.name:
                    continue
                for f in info.fences:
                    drop_fence(
                        f,
                        f"alternative to `{chosen}` "
                        f"(route {best.route.name} chosen: {why})",
                    )
            infos = {best.route.name: best}

    _drop_variants(infos, res, drop_fence, own, _system_pm)
    res.skipped.sort(
        key=lambda s: (
            s.evidence[0].file if s.evidence else "",
            (s.evidence[0].line_start or 0) if s.evidence else 0,
        )
    )
    return res


def _drop_variants(
    infos: dict[str, _RouteInfo],
    res: Resolution,
    drop_fence: Callable[..., None],
    own: _Own,
    system_pm: Callable[[str], str | None],
) -> None:
    """Within a route, commands with the same tool and subcommand that differ only in
    flags or arguments are variants: keep the shortest, drop the rest and the fences
    that exist only to support them."""
    for info in infos.values():
        groups: dict[tuple[str, ...], list[tuple[_Fence, Any]]] = {}
        for f in info.fences:
            for c in f.cands:
                if c.cmd.id in res.drop:
                    continue
                r = classify(c.text, own, system_pm)
                if r is not None and r.name == info.route.name:
                    groups.setdefault(_variant_key(c.text), []).append((f, c))
        for members in groups.values():
            if len(members) < 2:
                continue
            keep = min(members, key=lambda m: (len(_tokens(m[1].text)), m[0].order))
            for f, c in members:
                if c is keep[1] or c.cmd.id in res.drop:
                    continue
                reason = (
                    f"variant of `{keep[1].text}` "
                    f"(the simplest {info.route.name} command was kept)"
                )
                if f is keep[0]:
                    # shares its fence with the kept command: drop only this one
                    res.drop.add(c.cmd.id)
                    res.skipped.append(_skip(c, reason))
                else:
                    drop_fence(
                        f,
                        reason,
                        mate_reason=f"only needed by `{c.text}`, which was not chosen",
                        lead=c,
                    )
