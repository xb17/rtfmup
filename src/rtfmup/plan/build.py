"""Analysis + HostProfile -> Plan (PLAN §2.3, §2.5).

Order: install -> env -> setup -> build -> run -> healthcheck. (Runtime problems are
`Gap`s, not steps, until M3 adds providers.)

Nothing here executes anything. It reads a few manifest files (as `resolve` does) to
choose between yarn v1 and Berry and to find a compose file.

Choices the plan depends on are never guessed: when an ambiguity in the analysis has
no answer, `build_plan` raises `Unanswered` and the caller asks.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import posixpath
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rtfmup.host.probe import HostProfile
from rtfmup.models import Ambiguity, Analysis, Command, Component, SourceRef
from rtfmup.plan.model import Gap, Plan, Step, StepKind
from rtfmup.plan.translate import split_argv, to_windows
from rtfmup.resolve import manifest_run_candidates

PLAN_CATEGORIES = ("install", "setup", "build", "run")  # test/lint/other never run


@dataclass(frozen=True)
class Choices:
    """Answers to the analysis' ambiguities (also the `--variant/--component` flags)."""

    variant: str | None = None
    component: str | None = None
    run: str | None = None  # one of the amb-run option texts


class Unanswered(Exception):
    def __init__(self, questions: list[Ambiguity]):
        super().__init__("; ".join(q.question for q in questions))
        self.questions = questions


class BadChoice(ValueError):
    pass


# --- choices ---------------------------------------------------------------------


def _amb(a: Analysis, amb_id: str) -> Ambiguity | None:
    return next((x for x in a.ambiguities if x.id == amb_id), None)


def pending_questions(a: Analysis, choices: Choices) -> list[Ambiguity]:
    """Ambiguities the plan needs answered and that have no answer or default."""
    given = {
        "amb-component": choices.component,
        "amb-variant": choices.variant,
        "amb-run": choices.run,
    }
    return [
        amb
        for amb in a.ambiguities
        if amb.id in given and not given[amb.id] and amb.default is None
    ]


def _validate(a: Analysis, choices: Choices) -> None:
    for amb_id, value in (
        ("amb-component", choices.component),
        ("amb-variant", choices.variant),
        ("amb-run", choices.run),
    ):
        amb = _amb(a, amb_id)
        if value and amb and value not in amb.options:
            raise BadChoice(
                f"{value!r} is not one of: {', '.join(amb.options)} ({amb.question})"
            )
    if choices.component and choices.component not in {c.id for c in a.components}:
        raise BadChoice(f"unknown component {choices.component!r}")


def _select_component(a: Analysis, choices: Choices) -> Component:
    comps = a.components or [
        Component(
            id="root",
            path=".",
            runtimes=[],
            package_managers=[],
            runnable=False,
        )
    ]
    wanted = choices.component
    if not wanted and (amb := _amb(a, "amb-component")):
        wanted = amb.default
    if wanted:
        return next(c for c in comps if c.id == wanted)
    runnable = [c for c in comps if c.runnable]
    if len(runnable) == 1:
        return runnable[0]
    return next((c for c in comps if c.id == "root"), comps[0])


def _select_variant(a: Analysis, choices: Choices) -> str | None:
    if choices.variant:
        return choices.variant
    amb = _amb(a, "amb-variant")
    return amb.default if amb else None


# --- repo identity (carry-in 1: skip `git clone` of the repo we are already in) ---


def repo_key(url: str) -> str:
    """`git@github.com:o/r.git` and `https://github.com/o/r/` -> `github.com/o/r`."""
    u = url.strip().lower()
    if m := re.match(r"^[\w.-]+@([\w.-]+):(.+)$", u):
        u = f"{m.group(1)}/{m.group(2)}"
    else:
        u = re.sub(r"^[a-z+]+://", "", u)
        u = re.sub(r"^[^@/]+@", "", u)
    u = u.rstrip("/")
    return u.removesuffix(".git").rstrip("/")


_CLONE_ARG_OPTS = {
    "-b",
    "--branch",
    "--depth",
    "-o",
    "--origin",
    "-c",
    "--config",
    "--reference",
    "--template",
    "-j",
    "--jobs",
    "--filter",
    "--separate-git-dir",
}


def parse_clone(text: str) -> tuple[str, str] | None:
    """`git clone [opts] <url> [dest]` -> (url, dest dir name)."""
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return None
    if tokens[:2] != ["git", "clone"]:
        return None
    positional: list[str] = []
    it = iter(tokens[2:])
    for tok in it:
        if tok.startswith("-"):
            if tok in _CLONE_ARG_OPTS:
                next(it, None)
            continue
        positional.append(tok)
    if not positional:
        return None
    url = positional[0]
    dest = (
        positional[1]
        if len(positional) > 1
        else posixpath.basename(repo_key(url).rstrip("/"))
    )
    return url, posixpath.basename(dest.rstrip("/\\"))


def _is_own_clone(url: str, root: Path, own_urls: Sequence[str]) -> bool:
    if own_urls:
        return repo_key(url) in {repo_key(u) for u in own_urls}
    # Not a git repo, or no remotes: fall back to the directory name.
    return posixpath.basename(repo_key(url)) == root.name.lower()


# --- the README command walk -------------------------------------------------------

_CD = re.compile(r"""^cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s&;|]+)\s*$""")
_CD_AND = re.compile(
    r"""^cd\s+(?P<dir>"[^"]*"|'[^']*'|[^\s&;|]+)\s*&&\s*(?P<rest>.+)$"""
)
_ENV_COPY = re.compile(r"^(?:cp|copy)\s+\S*\.env\S*\s+\S*\.env\S*\s*$", re.IGNORECASE)
_OS_VARIANTS = {"windows", "macos", "linux"}

_PM_WORDS = {
    "npm": "npm",
    "npx": "npm",
    "pnpm": "pnpm",
    "yarn": "yarn",
    "bun": "bun",
    "bunx": "bun",
    "uv": "uv",
    "uvx": "uv",
    "pip": "pip",
    "pip3": "pip",
    "poetry": "poetry",
    "pipenv": "pipenv",
    "cargo": "cargo",
    "go": "go",
    "bundle": "bundler",
    "bundler": "bundler",
    "composer": "composer",
    "dotnet": "dotnet",
}
# Package managers that install into the same ecosystem slot: a README `pip install`
# replaces our default `uv pip install`, and the reverse.
_FAMILY = {"pip": "python-env", "uv": "python-env"}


def pm_of(text: str) -> str | None:
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        return None
    while tokens and tokens[0] in ("sudo", "env"):
        tokens = tokens[1:]
    if not tokens:
        return None
    if tokens[0] in ("python", "python3", "py") and tokens[1:3] == ["-m", "pip"]:
        return "pip"
    return _PM_WORDS.get(tokens[0])


_ECOSYSTEM = {
    "npm": "js",
    "pnpm": "js",
    "yarn": "js",
    "bun": "js",
    "uv": "py",
    "pip": "py",
    "poetry": "py",
    "pipenv": "py",
}


def _family(pm: str | None) -> str | None:
    return _FAMILY.get(pm or "", pm)


def _platform_ok(platform: str, host_os: str) -> bool:
    return (
        platform == "any"
        or platform == host_os
        or (platform == "unix" and host_os in ("linux", "macos"))
    )


@dataclass
class _Cand:
    cmd: Command
    text: str
    cwd: str
    env: dict[str, str]


@dataclass
class _Walk:
    cands: list[_Cand] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)


def _variant_ok(cmd: Command, variant: str | None, host_os: str) -> bool:
    if cmd.variant is None:
        return True
    if cmd.variant in _OS_VARIANTS:
        return cmd.variant == host_os
    if cmd.variant == "docker":
        return variant == "docker"
    return variant != "docker"  # "native" and anything else


def _resolve_cd(root: Path, cwd: str, target: str, own_names: set[str]) -> str | None:
    """New cwd, `cwd` itself for a cd into the clone we are already in, or None."""
    t = target.strip("\"'").rstrip("/\\")
    if not t or t in ("~", "-") or re.match(r"^([A-Za-z]:|[~/\\$%])", t):
        return None
    rel = posixpath.normpath(posixpath.join(cwd, t.replace("\\", "/")))
    if rel == ".." or rel.startswith("../"):
        return None
    if (root / rel).is_dir():
        return rel
    if t.lower() in own_names:
        return cwd
    return None


def _owner(components: list[Component], cwd: str) -> Component | None:
    best: Component | None = None
    for c in components:
        inside = c.path == "." or cwd == c.path or cwd.startswith(c.path + "/")
        if inside and (best is None or len(c.path) > len(best.path)):
            best = c
    return best


def _walk(
    a: Analysis,
    comp: Component,
    variant: str | None,
    host_os: str,
    own_urls: Sequence[str],
) -> _Walk:
    root = Path(a.root)
    out = _Walk()
    own_names = {root.name.lower()}
    state: dict[tuple[str, ...], dict] = {}
    for c in a.commands:
        if not c.evidence[0].detector.startswith("readme."):
            continue
        st = state.setdefault(tuple(c.section_path), {"cwd": ".", "env": {}})
        text = c.text.strip()

        if clone := parse_clone(text):
            url, dest = clone
            if _is_own_clone(url, root, own_urls):
                own_names.add(dest.lower())
                continue

        if m := _CD.match(text):
            new = _resolve_cd(root, st["cwd"], m.group("dir"), own_names)
            if new is None:
                out.gaps.append(
                    Gap(
                        kind="unresolved",
                        message=(
                            f"README runs `{text}` ({_where(c)}), which is not a "
                            "directory in this repo. The commands after it run from "
                            f"`{st['cwd']}` instead."
                        ),
                    )
                )
            else:
                st["cwd"] = new
            continue

        cwd = st["cwd"]
        if m := _CD_AND.match(text):
            new = _resolve_cd(root, cwd, m.group("dir"), own_names)
            if new is None:
                out.gaps.append(
                    Gap(
                        kind="unresolved",
                        message=f"README runs `{text}` ({_where(c)}); its `cd` target is not in this repo.",
                    )
                )
                continue
            cwd, text = new, m.group("rest").strip()

        if c.category not in PLAN_CATEGORIES or not _variant_ok(c, variant, host_os):
            # Still honour `export` in sections we otherwise skip? No: skipped means skipped.
            continue

        translated = to_windows(text) if text.startswith("export") else None
        if translated and translated.env and not translated.shell_text:
            secret = re.compile(
                r"KEY|SECRET|TOKEN|PASSWORD|PASS|PRIVATE", re.IGNORECASE
            )
            for k, v in translated.env.items():
                if secret.search(k) or "placeholder" in c.flags:
                    out.gaps.append(
                        Gap(
                            kind="missing_env",
                            message=f"README exports {k} ({_where(c)}); set it yourself, rtfmup will not store its value.",
                        )
                    )
                else:
                    st["env"][k] = v
            continue

        owner = _owner(a.components, cwd)
        if owner is not None and owner.id != comp.id and cwd != ".":
            continue  # belongs to another component
        step_cwd = cwd
        if cwd == "." and comp.path != ".":
            pm = pm_of(text)
            if pm is not None and pm not in comp.package_managers:
                continue  # a package-manager command for some other component
            if pm is not None:
                step_cwd = comp.path
        out.cands.append(_Cand(c, text, step_cwd, dict(st["env"])))
    return out


def _where(c: Command) -> str:
    ev = c.evidence[0]
    return f"{ev.file}:{ev.line_start}" if ev.line_start else ev.file


# --- manifest defaults (PLAN §2.5) ---------------------------------------------------


@dataclass
class _Default:
    text: str
    pm: str
    title: str
    evidence: list[SourceRef]
    cwd: str


def _yarn_is_berry(root: Path, comp: Component) -> bool:
    base = root / comp.path
    if (base / ".yarnrc.yml").is_file() or (root / ".yarnrc.yml").is_file():
        return True
    try:
        pkg = json.loads((base / "package.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    m = re.match(r"yarn@(\d+)", str(pkg.get("packageManager", "")))
    return bool(m and int(m.group(1)) >= 2)


def _default_installs(a: Analysis, comp: Component) -> list[_Default]:
    root = Path(a.root)
    out: list[_Default] = []
    for pm in a.package_managers:
        if pm.component != comp.id:
            continue
        lock = pm.lockfile
        why = f"from {lock}" if lock else "from the manifest"
        steps: list[str]
        match pm.name:
            case "uv":
                steps = ["uv sync"]
            case "poetry":
                steps = ["poetry install"]
            case "pip":
                steps = ["uv venv", "uv pip install -r requirements.txt"]
            case "npm":
                steps = ["npm ci" if lock == "package-lock.json" else "npm install"]
            case "pnpm":
                steps = ["pnpm install --frozen-lockfile"]
            case "yarn":
                berry = _yarn_is_berry(root, comp)
                steps = [
                    "yarn install --immutable"
                    if berry
                    else "yarn install --frozen-lockfile"
                ]
            case "bun":
                steps = ["bun install --frozen-lockfile"]
            case "cargo":
                steps = ["cargo build"]
            case "go":
                steps = ["go mod download"]
            case "bundler":
                steps = ["bundle install"]
            case "composer":
                steps = ["composer install"]
            case "dotnet":
                steps = ["dotnet restore"]
            case _:
                continue
        for text in steps:
            out.append(
                _Default(text, pm.name, f"{text} ({why})", pm.evidence, comp.path)
            )
    return out


def _compose_run(a: Analysis) -> tuple[str, str, list[SourceRef]] | None:
    for m in a.manifests:
        if m.kind == "compose":
            directory = posixpath.dirname(m.file) or "."
            name = posixpath.basename(m.file)
            standard = {
                "compose.yaml",
                "compose.yml",
                "docker-compose.yaml",
                "docker-compose.yml",
            }
            text = (
                "docker compose up"
                if name in standard
                else f"docker compose -f {name} up"
            )
            return text, directory, m.evidence
    return None


_PROD_SCRIPTS = {"start", "serve", "preview"}


def _node_run_text(comp: Component, a: Analysis, script: str) -> str:
    pm = next(
        (
            p.name
            for p in a.package_managers
            if p.component == comp.id and p.name in ("npm", "pnpm", "yarn", "bun")
        ),
        "npm",
    )
    return f"{pm} run {script}"


# --- step construction -----------------------------------------------------------------


class _Ids:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"step-{self.n}"


def _command_step(
    ids: _Ids,
    kind: StepKind,
    title: str,
    text: str,
    *,
    cwd: str,
    env: dict[str, str],
    platform: str,
    needs_shell: bool,
    host_os: str,
    from_commands: list[str],
    evidence: list[SourceRef],
    long_running: bool = False,
    shell: str = "bash",
) -> Step:
    argv: list[str] | None = None
    shell_text: str | None = None
    ok = _platform_ok(platform, host_os)
    translated = to_windows(text) if host_os == "windows" else None
    if translated and translated.shell_text:
        shell_text, ok = translated.shell_text, True
    elif needs_shell or shell in ("powershell", "cmd"):
        shell_text = text  # POSIX splitting would eat Windows backslashes
    else:
        argv = split_argv(text)
        if argv is None:
            shell_text = text
    return Step(
        id=ids.next(),
        kind=kind,
        title=title,
        argv=argv,
        shell_text=shell_text,
        cwd=cwd,
        env=env,
        platform_ok=ok,
        from_commands=from_commands,
        evidence=evidence,
        long_running=long_running,
    )


def analysis_ref(a: Analysis) -> str:
    data = (a.model_dump_json(indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def build_plan(
    a: Analysis,
    host: HostProfile,
    choices: Choices | None = None,
    *,
    own_urls: Sequence[str] = (),
) -> Plan:
    choices = choices or Choices()
    _validate(a, choices)
    if questions := pending_questions(a, choices):
        raise Unanswered(questions)

    comp = _select_component(a, choices)
    variant = _select_variant(a, choices)
    host_os = host.os
    walk = _walk(a, comp, variant, host_os, own_urls)
    ids = _Ids()
    gaps = list(walk.gaps)

    def readme(category: str) -> list[_Cand]:
        """README commands of a category; a command repeated in several sections
        (bash and PowerShell variants, per-OS blocks) runs once."""
        seen: set[tuple[str, str]] = set()
        out: list[_Cand] = []
        for c in walk.cands:
            key = (c.text, c.cwd)
            if c.cmd.category == category and key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def from_cand(kind: StepKind, c: _Cand, **extra) -> Step:
        return _command_step(
            ids,
            kind,
            c.text,
            c.text,
            cwd=c.cwd,
            env=c.env,
            platform=c.cmd.platform,
            needs_shell=c.cmd.needs_shell,
            host_os=host_os,
            from_commands=[c.cmd.id],
            evidence=c.cmd.evidence,
            shell=c.cmd.shell,
            **extra,
        )

    steps: list[Step] = []

    # install: manifest defaults unless the README covers that package-manager family
    readme_installs = _manifests_win(a, comp, readme("install"), gaps)
    covered = {_family(pm_of(c.text)) for c in readme_installs}
    if variant != "docker":
        for d in _default_installs(a, comp):
            if _family(d.pm) in covered:
                continue
            steps.append(
                _command_step(
                    ids,
                    "install",
                    d.title,
                    d.text,
                    cwd=d.cwd,
                    env={},
                    platform="any",
                    needs_shell=False,
                    host_os=host_os,
                    from_commands=[],
                    evidence=d.evidence,
                )
            )
    steps += [from_cand("install", c) for c in readme_installs]

    # env: one step from the template; the README's `cp .env.example .env` folds into it
    template = next(
        (
            m
            for m in a.manifests
            if m.kind == "env_template"
            and _owner(a.components, posixpath.dirname(m.file) or ".") in (comp, None)
        ),
        None,
    )
    if a.env and template is not None:
        steps.append(
            Step(
                id=ids.next(),
                kind="env",
                title=f"Create .env from {template.file} and fill in required values",
                cwd=posixpath.dirname(template.file) or ".",
                evidence=template.evidence,
            )
        )
        env_step_added = True
    else:
        env_step_added = False

    steps += [
        from_cand("setup", c)
        for c in readme("setup")
        if not (env_step_added and _ENV_COPY.match(c.text))
    ]

    # run
    run_step = _choose_run(a, comp, variant, choices, walk, ids, host_os)
    if run_step is None:
        gaps.append(
            Gap(
                kind="unresolved",
                message="No run command found in the README or the manifests.",
            )
        )

    # build: only when the chosen run step needs it
    builds = readme("build")
    if run_step is not None and _build_needed(a, comp, run_step, builds, walk):
        steps += [from_cand("build", c) for c in builds]

    health = None
    if run_step is not None:
        steps.append(run_step)
        health = _health_port(a, comp)
        if health is not None:
            steps.append(
                Step(
                    id=ids.next(),
                    kind="healthcheck",
                    title=f"Wait for {health.url or f'port {health.port}'}",
                    evidence=health.evidence,
                )
            )

    for s in steps:
        if not s.platform_ok:
            gaps.append(
                Gap(
                    kind="unsupported_platform",
                    message=f"`{s.title}` needs a different OS than {host_os} ({_ev(s)}).",
                )
            )

    return Plan(
        analysis_ref=analysis_ref(a),
        host_os=host.os,
        host_arch=host.arch,
        variant=variant,
        component=comp.id,
        steps=steps,
        gaps=gaps,
        health=health,
    )


def _manifests_win(
    a: Analysis, comp: Component, installs: list[_Cand], gaps: list[Gap]
) -> list[_Cand]:
    """D15: drop README installs that contradict the manifests, and say so."""
    detected = {pm.name for pm in a.package_managers if pm.component == comp.id}
    root = Path(a.root)
    keep: list[_Cand] = []
    for c in installs:
        pm = pm_of(c.text)
        eco = _ECOSYSTEM.get(pm or "")
        same_eco = {d for d in detected if _ECOSYSTEM.get(d) == eco}
        if eco and same_eco and _family(pm) not in {_family(d) for d in same_eco}:
            gaps.append(
                Gap(
                    kind="unresolved",
                    message=(
                        f"README says `{c.text}` ({_where(c.cmd)}) but this project "
                        f"uses {', '.join(sorted(same_eco))}; the manifests win."
                    ),
                )
            )
            continue
        if missing := _missing_requirements(root, c):
            gaps.append(
                Gap(
                    kind="unresolved",
                    message=(
                        f"README says `{c.text}` ({_where(c.cmd)}) but `{missing}` "
                        "does not exist in the repo; skipped."
                    ),
                )
            )
            continue
        keep.append(c)
    return keep


def _missing_requirements(root: Path, c: _Cand) -> str | None:
    try:
        tokens = shlex.split(c.text, posix=True)
    except ValueError:
        return None
    for flag, value in itertools.pairwise(tokens):
        if flag in ("-r", "--requirement") and not (root / c.cwd / value).is_file():
            return value
    return None


def _health_port(a: Analysis, comp: Component):
    """The app's port: assigned to this component, else not a service's (DB) port."""
    service_ports = {p for svc in a.services for p in svc.ports}
    own = [p for p in a.ports if p.component == comp.id]
    loose = [p for p in a.ports if p.component is None and p.port not in service_ports]
    pool = own or loose
    return pool[0] if pool else None


def _ev(s: Step) -> str:
    if not s.evidence:
        return "manifest"
    e = s.evidence[0]
    return f"{e.file}:{e.line_start}" if e.line_start else e.file


def _choose_run(
    a: Analysis,
    comp: Component,
    variant: str | None,
    choices: Choices,
    walk: _Walk,
    ids: _Ids,
    host_os: str,
) -> Step | None:
    root = Path(a.root)
    readme_runs = [c for c in walk.cands if c.cmd.category == "run"]
    amb = _amb(a, "amb-run")
    want_manifest = bool(
        amb and choices.run and choices.run != amb.options[0]
    )  # picked the manifest option
    common = {"long_running": True}

    def readme_step(c: _Cand) -> Step:
        return _command_step(
            ids,
            "run",
            c.text,
            c.text,
            cwd=c.cwd,
            env=c.env,
            platform=c.cmd.platform,
            needs_shell=c.cmd.needs_shell,
            host_os=host_os,
            from_commands=[c.cmd.id],
            evidence=c.cmd.evidence,
            shell=c.cmd.shell,
            **common,
        )

    if readme_runs and not want_manifest:
        return readme_step(readme_runs[0])

    if variant == "docker":
        if (compose := _compose_run(a)) is not None:
            text, cwd, ev = compose
            return _command_step(
                ids,
                "run",
                f"{text} (from the compose file)",
                text,
                cwd=cwd,
                env={},
                platform="any",
                needs_shell=False,
                host_os=host_os,
                from_commands=[],
                evidence=ev,
                **common,
            )
        return None

    for canon, ref in manifest_run_candidates(root, comp):
        text = (
            _node_run_text(comp, a, canon.removeprefix("node:"))
            if canon.startswith("node:")
            else canon
        )
        return _command_step(
            ids,
            "run",
            f"{text} (from {ref.file})",
            text,
            cwd=comp.path,
            env={},
            platform="any",
            needs_shell=False,
            host_os=host_os,
            from_commands=[],
            evidence=[ref],
            **common,
        )
    return None


def _build_needed(
    a: Analysis,
    comp: Component,
    run: Step,
    builds: list[_Cand],
    walk: _Walk,
) -> bool:
    """A README build step runs only if the run step needs built output.

    That is the case when (a) the run step is a production-style script (`start`,
    `serve`, `preview`) of a component that also has a `build` script, or (b) the
    README presents the build and the run command in the same section.
    """
    if not builds:
        return False
    text = " ".join(run.argv or []) or (run.shell_text or "")
    m = re.match(r"^(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?([\w:.-]+)", text)
    if m and m.group(1) in _PROD_SCRIPTS and "build" in comp.scripts:
        return True
    run_cmd = next(
        (c.cmd for c in walk.cands if run.from_commands == [c.cmd.id]),
        None,
    )
    if run_cmd is None:
        return False
    return any(b.cmd.section_path == run_cmd.section_path for b in builds)
