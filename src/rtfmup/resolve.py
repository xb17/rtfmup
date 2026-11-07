"""Pass 3: conflicts (D15) and ambiguities between manifests and README. No LLM.

Manifests beat the README. Where they disagree we record a Conflict with
evidence from both sides, and where the README offers more than one path we
record an Ambiguity and ask rather than guess.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from rtfmup.models import (
    Ambiguity,
    Command,
    Component,
    Conflict,
    PackageManager,
    Runtime,
    SourceRef,
)

# --- version comparison (minimal; the full comparator is D13 / T2.1) ---------------

Version = tuple[int, ...]
_NUM = re.compile(r"\d+(?:\.\d+)*")
_UNPARSEABLE = re.compile(
    r"(?i)^\s*(?:\*|latest|stable|nightly|beta|lts(?:/.*)?|x|system)\s*$"
)


def parse_floor(spec: str) -> tuple[Version, bool] | None:
    """(lowest version a spec allows, spec_is_exact) or None if it can't be read.

    Handles `20`, `20.x`, `v18.19.0`, `go1.22.3`, `net8.0`, `>=3.11,<4`, `^18`,
    `~3.11`, `~=3.11`, `^16 || ^18`. Anything else is "can't verify" (D13).
    """
    if _UNPARSEABLE.match(spec):
        return None
    floors: list[Version] = []
    exact = True
    for alt in spec.split("||"):
        alt = alt.strip()
        if not alt:
            return None
        parts = [p.strip() for p in re.split(r"[,\s]+(?=[<>=~^!])|,", alt) if p.strip()]
        lower = next(
            (p for p in parts if p.startswith((">=", "^", "~", "==", ">"))), None
        )
        has_op = any(p[0] in "<>=~^!" for p in parts)
        target = lower or (parts[0] if parts else alt)
        m = _NUM.search(target)
        if not m:
            return None
        floors.append(tuple(int(x) for x in m.group(0).split(".")))
        exact = exact and not has_op and "x" not in alt.lower().split(".")[-1:]
    return min(floors), exact


def _trunc_cmp(a: Version, b: Version) -> int:
    n = min(len(a), len(b))
    return (a[:n] > b[:n]) - (a[:n] < b[:n])


def versions_conflict(readme: str, manifest: str) -> bool:
    """True when the README's claim can't be right given the manifest's spec."""
    r = parse_floor(readme)
    m = parse_floor(manifest)
    if r is None or m is None:
        return False  # can't verify: shown to the user, never guessed
    (rv, _), (mv, m_exact) = r, m
    r_plus = readme.strip().endswith("+")
    if m_exact:
        # The manifest pins one version. "N+" needs pin >= N; "N" needs the same prefix.
        cmp = _trunc_cmp(mv, rv)
        return cmp < 0 if r_plus else cmp != 0
    # The manifest is a range: the README must not promise versions below its floor.
    return _trunc_cmp(rv, mv) < 0


# --- README -> filesystem references -----------------------------------------------

_FILE_EXT = (
    ".txt", ".json", ".yml", ".yaml", ".toml", ".lock", ".cfg", ".ini", ".py", ".sh",
    ".ps1", ".bat", ".js", ".ts", ".mjs", ".rb", ".php", ".go", ".rs", ".csproj", ".sln",
    ".env", ".example", ".sample", ".md",
)  # fmt: skip
_BARE_FILES = {"Makefile", "Dockerfile", "Gemfile", "Procfile", "Justfile", "Taskfile"}
_GENERATED_DIRS = {
    "venv", ".venv", "env", "node_modules", "dist", "build", "target", "out", ".next",
    "__pycache__", ".git", "vendor", "bin", "obj", ".tox", ".mypy_cache", "coverage",
}  # fmt: skip
_SKIP_TOKEN = re.compile(r"[<>{}$*?|`\\]|://|^[~/]|^[A-Za-z]:|^@|@.+:")
_OUTPUT_COMMANDS = {"cp", "copy", "mv", "move", "touch", "tee", "mkdir", "curl", "wget"}


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def referenced_files(text: str) -> list[str]:
    """Repo-relative file paths a README command expects to already exist."""
    out: list[str] = []
    for seg in re.split(r"&&|\|\||;|\|", text):
        toks = _tokens(seg.strip())
        if not toks:
            continue
        head = toks[0].lower()
        if head == "cd":
            continue
        args = toks[1:]
        if head in _OUTPUT_COMMANDS:
            if head in ("cp", "copy", "mv", "move"):
                args = args[:-1]  # the last argument is the destination
            else:
                continue
        prev = ""
        skip_next = False
        for arg in args:
            if skip_next:  # target of a redirection: created by the shell, not read
                skip_next = False
                continue
            if re.fullmatch(r"\d*>>?", arg):
                skip_next = True
                continue
            if arg.startswith(">"):
                continue
            token = arg
            if arg.startswith("-") and "=" in arg:  # --file=x.yml
                token = arg.split("=", 1)[1]
            elif arg.startswith("-"):
                prev = arg
                continue
            if prev in ("-o", "--output", "--outfile", "-O") or _SKIP_TOKEN.search(
                token
            ):
                prev = ""
                continue
            prev = ""
            first = token.replace("\\", "/").split("/")[0]
            if first in _GENERATED_DIRS or token in (".", ".."):
                continue
            name = token.rsplit("/", 1)[-1]
            if (
                name in _BARE_FILES
                or name.lower().endswith(_FILE_EXT)
                or name.startswith(".env")
            ):
                out.append(token)
    return list(dict.fromkeys(out))


def _cd_targets(text: str) -> list[str]:
    return [
        t[1]
        for seg in re.split(r"&&|;", text)
        if len(t := _tokens(seg.strip())) >= 2 and t[0] == "cd"
    ]


def file_conflicts(
    commands: list[Command], root: Path, components: list[Component]
) -> list[Conflict]:
    bases = [root] + [root / c.path for c in components if c.path != "."]
    out: list[Conflict] = []
    seen_cd: dict[tuple[str, ...], list[str]] = {}
    for cmd in commands:
        if not cmd.evidence[0].detector.startswith("readme."):
            continue
        key = tuple(cmd.section_path)
        cds = seen_cd.setdefault(key, [])
        here = [*cds, *_cd_targets(cmd.text)]
        cds.extend(_cd_targets(cmd.text))
        if "placeholder" in cmd.flags:
            continue
        search = [*bases, *[root / d for d in here]]
        for rel in referenced_files(cmd.text):
            if any((b / rel).exists() for b in search):
                continue
            out.append(
                Conflict(
                    id="",
                    message=f"README runs `{cmd.text}` but `{rel}` does not exist in the repository",
                    evidence=list(cmd.evidence),
                )
            )
    return out


# --- runtime versions ---------------------------------------------------------------


def runtime_conflicts(runtimes: list[Runtime]) -> list[Conflict]:
    readme = [
        r
        for r in runtimes
        if r.evidence[0].detector == "readme.version" and r.version_spec
    ]
    manifests = [
        r
        for r in runtimes
        if not r.evidence[0].detector.startswith("readme.") and r.version_spec
    ]
    out: list[Conflict] = []
    for r in readme:
        for m in manifests:
            if r.name == m.name and versions_conflict(
                r.version_spec or "", m.version_spec or ""
            ):
                out.append(
                    Conflict(
                        id="",
                        message=(
                            f"README says {r.name} {r.version_spec} but "
                            f"{m.evidence[0].file} says {m.version_spec}"
                        ),
                        evidence=[*r.evidence, *m.evidence],
                    )
                )
    return out


# --- package managers ---------------------------------------------------------------

FAMILIES: dict[str, set[str]] = {
    "node": {"npm", "pnpm", "yarn", "bun"},
    "python": {"pip", "uv", "poetry", "pipenv"},
}
_PM_COMMAND = re.compile(
    r"^(?:sudo\s+)?(?P<pm>npm|pnpm|yarn|bun|pip3?|uv|poetry|pipenv)\b(?P<rest>.*)$"
)


def readme_manager(text: str) -> str | None:
    """Package manager a README install command uses, normalized (`pip3` -> `pip`)."""
    first = re.split(r"&&|;", text)[-1 if text.strip().startswith("cd ") else 0].strip()
    if re.match(r"^(?:python3?|py)\s+-m\s+pip\b", first):
        return "pip"
    m = _PM_COMMAND.match(first)
    if not m:
        return None
    pm = m.group("pm")
    if pm == "uv" and not re.match(
        r"\s+(?:sync|add|pip|venv|lock|run)\b", m.group("rest")
    ):
        return None
    return "pip" if pm.startswith("pip") else pm


def _declared(pm: PackageManager) -> bool:
    """A manager we can trust: it has a lockfile or was declared (`packageManager`)."""
    return pm.lockfile is not None or any(
        e.detector == "node.package_manager_field" for e in pm.evidence
    )


def manager_conflicts(
    commands: list[Command], managers: list[PackageManager]
) -> list[Conflict]:
    out: list[Conflict] = []
    trusted = [p for p in managers if _declared(p)]
    for cmd in commands:
        if cmd.category != "install" or not cmd.evidence[0].detector.startswith(
            "readme."
        ):
            continue
        used = readme_manager(cmd.text)
        if used is None:
            continue
        family = next((f for f, names in FAMILIES.items() if used in names), None)
        if family is None:
            continue
        detected = [p for p in trusted if p.name in FAMILIES[family]]
        if not detected or any(p.name == used for p in detected):
            continue
        ev = [*cmd.evidence, *detected[0].evidence[:1]]
        names = ", ".join(sorted({p.name for p in detected}))
        out.append(
            Conflict(
                id="",
                message=f"README installs with `{used}` but the repository uses {names} ({detected[0].lockfile or 'declared'})",
                evidence=ev,
            )
        )
    return out


def find_conflicts(
    commands: list[Command],
    runtimes: list[Runtime],
    managers: list[PackageManager],
    root: Path,
    components: list[Component],
) -> list[Conflict]:
    found = [
        *file_conflicts(commands, root, components),
        *runtime_conflicts(runtimes),
        *manager_conflicts(commands, managers),
    ]
    found.sort(
        key=lambda c: (c.evidence[0].file, c.evidence[0].line_start or 0, c.message)
    )
    unique: list[Conflict] = []
    for c in found:
        if not any(u.message == c.message for u in unique):
            unique.append(c)
    return [
        c.model_copy(update={"id": f"conflict-{i}"}) for i, c in enumerate(unique, 1)
    ]


# --- ambiguities ----------------------------------------------------------------------

_OS_VARIANTS = {"windows", "macos", "linux"}  # decided by the host, not the user


def variant_ambiguity(commands: list[Command]) -> Ambiguity | None:
    """Native vs Docker: both paths have a run command."""
    runs = [
        c
        for c in commands
        if c.category == "run"
        and c.evidence[0].detector.startswith("readme.")
        and c.variant not in _OS_VARIANTS
    ]
    by_variant: dict[str, Command] = {}
    for c in runs:
        by_variant.setdefault("docker" if c.variant == "docker" else "native", c)
    if len(by_variant) < 2:
        return None
    return Ambiguity(
        id="amb-variant",
        question="Two setup paths found: native or Docker. Which one?",
        options=sorted(by_variant),
        default=None,
        evidence=[by_variant[k].evidence[0] for k in sorted(by_variant)],
    )


_NODE_RUN = re.compile(r"^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?P<script>[\w:.-]+)")


def canon_run(text: str) -> str:
    """Comparable form of a run command (`npm run dev` == `pnpm dev`)."""
    first = (
        re.split(r"&&|;", text)[-1].strip()
        if text.strip().startswith("cd ")
        else text.strip()
    )
    if m := _NODE_RUN.match(first):
        return f"node:{m.group('script')}"
    return " ".join(first.split())


def manifest_run_candidates(
    root: Path, component: Component
) -> list[tuple[str, SourceRef]]:
    """Default run commands implied by manifests alone (PLAN §2.5)."""
    base = root if component.path == "." else root / component.path
    pref = "" if component.path == "." else f"{component.path}/"

    def ref(name: str, detector: str) -> SourceRef:
        return SourceRef(
            file=f"{pref}{name}", line_start=1, line_end=1, detector=detector
        )

    out: list[tuple[str, SourceRef]] = []
    scripts = component.scripts
    for s in ("dev", "start"):
        if s in scripts:
            out.append((f"node:{s}", ref("package.json", "manifest.run.node_script")))
            break
    if (base / "Cargo.toml").is_file() and (base / "src" / "main.rs").is_file():
        out.append(("cargo run", ref("Cargo.toml", "manifest.run.cargo")))
    if (base / "go.mod").is_file() and (base / "main.go").is_file():
        out.append(("go run .", ref("go.mod", "manifest.run.go")))
    for csproj in sorted(base.glob("*.csproj")):
        out.append(
            (
                f"dotnet run --project {csproj.name}",
                ref(csproj.name, "manifest.run.dotnet"),
            )
        )
        break
    if (base / "bin" / "rails").is_file():
        out.append(("bin/rails server", ref("bin/rails", "manifest.run.rails")))
    if (base / "artisan").is_file():
        out.append(("php artisan serve", ref("artisan", "manifest.run.laravel")))
    out += _python_scripts(base, ref)
    return out


def _python_scripts(base: Path, ref) -> list[tuple[str, SourceRef]]:
    import tomllib

    try:
        data = tomllib.loads((base / "pyproject.toml").read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    project = data.get("project")
    scripts = project.get("scripts") if isinstance(project, dict) else None
    if not isinstance(scripts, dict) or not (base / "uv.lock").is_file():
        return []
    return [
        (f"uv run {name}", ref("pyproject.toml", "manifest.run.uv_script"))
        for name in sorted(scripts)
    ]


def run_ambiguity(
    commands: list[Command],
    components: list[Component],
    managers: list[PackageManager],
    root: Path,
) -> Ambiguity | None:
    """README run command(s) exist, a manifest default exists, and none agree."""
    runnable = [c for c in components if c.runnable]
    if len(runnable) != 1:
        return None  # several runnable components are already an ambiguity of their own
    readme_runs = [
        c
        for c in commands
        if c.category == "run"
        and c.evidence[0].detector.startswith("readme.")
        and c.variant not in ("docker", *_OS_VARIANTS)
        and "placeholder" not in c.flags
    ]
    candidates = manifest_run_candidates(root, runnable[0])
    if not readme_runs or not candidates:
        return None
    readme_canon = {canon_run(c.text) for c in readme_runs}
    if readme_canon & {canon for canon, _ in candidates}:
        return None
    first_readme = readme_runs[0]
    canon, ref = candidates[0]
    shown = canon.replace("node:", "npm run ") if canon.startswith("node:") else canon
    return Ambiguity(
        id="amb-run",
        question="The README and the manifests suggest different ways to run this. Which one?",
        options=[first_readme.text, shown],
        default=None,
        evidence=[first_readme.evidence[0], ref],
    )


def find_ambiguities(
    commands: list[Command],
    components: list[Component],
    managers: list[PackageManager],
    root: Path,
    component_ambiguities: list[Ambiguity],
) -> list[Ambiguity]:
    found = list(component_ambiguities)
    for amb in (
        variant_ambiguity(commands),
        run_ambiguity(commands, components, managers, root),
    ):
        if amb:
            found.append(amb)
    return sorted(found, key=lambda a: a.id)
