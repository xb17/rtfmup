"""Command-line entry point (typer)."""

from __future__ import annotations

import dataclasses
import json
import shutil
import signal
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rtfmup import __version__, ui
from rtfmup.analyze import analyze, remote_urls
from rtfmup.execute.health import DEFAULT_HEALTH_TIMEOUT
from rtfmup.execute.up import execute_plan
from rtfmup.exitcodes import EXIT_DECLINED, EXIT_USAGE
from rtfmup.export.devcontainer import build_devcontainer, linux_host
from rtfmup.host.probe import probe_host
from rtfmup.models import Ambiguity, Analysis
from rtfmup.plan.build import BadChoice, Choices, Unanswered, build_plan
from rtfmup.plan.model import Plan
from rtfmup.plan.render import render_plan
from rtfmup.plan.store import DEVCONTAINER_FILE, plan_json, write_plan, write_state_file
from rtfmup.schema_md import render_markdown
from rtfmup.source import SourceError, resolve

app = typer.Typer(
    name="rtfmup",
    help="Set up any repo from its README. (Early development: `scan`, `plan`, `doctor`, `export devcontainer` and `up` (Windows) work.)",
    add_completion=False,
    no_args_is_help=True,
)
export_app = typer.Typer(
    help="Export the analysis to other formats.", no_args_is_help=True
)
recipe_app = typer.Typer(help="Recorded setup recipes.", no_args_is_help=True)
app.add_typer(export_app, name="export")
app.add_typer(recipe_app, name="recipe")

TargetArg = Annotated[str, typer.Argument(help="Git URL or local path.")]


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"rtfmup {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Set up any repo from its README."""


def _stub(name: str) -> None:
    ui.not_implemented(name)
    raise typer.Exit(EXIT_USAGE)


_UP_STAGES = 4

QuietOpt = Annotated[
    bool,
    typer.Option(
        "--quiet",
        "-q",
        help="Only stage headers, the confirmation and the final line; "
        "a failing step's last 40 lines.",
    ),
]
VerboseOpt = Annotated[
    bool,
    typer.Option(
        "--verbose", "-v", help="Show the host probe table and analysis summary first."
    ),
]


def _check_verbosity(quiet: bool, verbose: bool) -> None:
    if quiet and verbose:
        ui.error("--quiet and --verbose cannot be used together.")
        raise typer.Exit(EXIT_USAGE)


def _console() -> Console:
    return Console(width=max(100, shutil.get_terminal_size().columns))


def _render_verbose(analysis: Analysis, host) -> None:
    """The host probe table and the analysis summary, ahead of the plan."""
    ui.render_host(host, _console())
    typer.echo("", err=True)
    ui.render_summary(analysis, _console())
    typer.echo("", err=True)


def _treat_break_as_interrupt() -> None:
    """Stop the child tree instead of orphaning it when we are told to stop.

    Windows: Ctrl+Break and console close act like Ctrl+C (Python only maps
    CTRL_C_EVENT to KeyboardInterrupt). Linux and macOS: SIGINT is restored to
    Python's handler, since a job started in the background by a shell inherits
    SIGINT as ignored, and SIGTERM (a CI cancel, `kill`) becomes the same interrupt.
    """

    def handler(signum, frame):
        raise KeyboardInterrupt

    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, handler)
        return
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


@app.command()
def up(
    target: TargetArg,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", "-y", help="Approve low and medium risk steps. Never high risk."
        ),
    ] = False,
    variant: Annotated[
        str | None, typer.Option(help="Setup path, e.g. native or docker.")
    ] = None,
    component: Annotated[
        str | None, typer.Option(help="Component id in a multi-component repo.")
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(help="Which run command to use when README and manifests differ."),
    ] = None,
    dest: Annotated[
        Path | None,
        typer.Option(help="Directory to clone URL targets into (default: the cache)."),
    ] = None,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Ignore the cached analysis.")
    ] = False,
    health_timeout: Annotated[
        float, typer.Option(help="Seconds to wait for the run step's port.")
    ] = DEFAULT_HEALTH_TIMEOUT,
    resume: Annotated[
        bool,
        typer.Option(
            "--resume",
            help="Continue the last run: skip steps that finished, if the plan is unchanged.",
        ),
    ] = False,
    quiet: QuietOpt = False,
    verbose: VerboseOpt = False,
) -> None:
    """Analyze TARGET, show the plan, confirm, set it up, and run it."""
    _check_verbosity(quiet, verbose)
    try:
        ui.stage(1, _UP_STAGES, "Resolving target")
        source = resolve(target, cache=dest)
        ui.stage(2, _UP_STAGES, "Analyzing")
        analysis = analyze(source.root, fresh=fresh)
        ui.stage(3, _UP_STAGES, "Probing this host (read-only)")
        host = probe_host()
    except (SourceError, NotADirectoryError) as exc:
        ui.error(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    result = _build_answered(
        source, analysis, host, Choices(variant=variant, component=component, run=run)
    )
    write_plan(source.root, result)
    ui.stage(4, _UP_STAGES, "Plan")
    if verbose:
        _render_verbose(analysis, host)
    if not quiet:  # the confirmation lists the commands and their risk
        render_plan(result, _console())
        typer.echo("", err=True)

    _treat_break_as_interrupt()
    outcome = execute_plan(
        result,
        analysis,
        source.root,
        assume_yes=yes,
        interactive=_is_interactive(),
        health_timeout=health_timeout,
        resume=resume,
        quiet=quiet,
    )
    if outcome.outcome in ("up", "stopped"):
        ui.done(outcome.message or "Stopped.")
    else:
        ui.error(outcome.message or outcome.outcome)
    raise typer.Exit(outcome.exit_code)


_FLAG_FOR = {
    "amb-variant": "--variant",
    "amb-component": "--component",
    "amb-run": "--run",
}


def _is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _ask(questions: list[Ambiguity], choices: Choices) -> Choices:
    """Ask each open question on the terminal and fold the answers into `choices`."""
    field = {"amb-variant": "variant", "amb-component": "component", "amb-run": "run"}
    answers: dict[str, str] = {}
    for q in questions:
        typer.echo(f"\n{q.question}", err=True)
        for i, opt in enumerate(q.options, 1):
            typer.echo(f"  {i}) {opt}", err=True)
        while True:
            raw = typer.prompt("Choice", err=True)
            if raw.strip().isdigit() and 1 <= int(raw) <= len(q.options):
                answers[field[q.id]] = q.options[int(raw) - 1]
                break
            if raw in q.options:
                answers[field[q.id]] = raw
                break
            typer.echo(f"Enter 1-{len(q.options)}.", err=True)
    return dataclasses.replace(choices, **answers)


def _build_answered(source, analysis: Analysis, host, choices: Choices) -> Plan:
    """build_plan, asking (or failing with the flag to pass) for open questions."""
    own = [*remote_urls(source.root), *([source.url] if source.url else [])]
    while True:
        try:
            return build_plan(analysis, host, choices, own_urls=own)
        except BadChoice as exc:
            ui.error(str(exc))
            raise typer.Exit(EXIT_USAGE) from exc
        except Unanswered as exc:
            if not _is_interactive():
                for q in exc.questions:
                    flag = _FLAG_FOR.get(q.id, "")
                    ui.error(
                        f"{q.question} Pass {flag} with one of: {', '.join(q.options)}"
                    )
                raise typer.Exit(EXIT_USAGE) from exc
            choices = _ask(exc.questions, choices)


@app.command()
def plan(
    target: Annotated[str, typer.Argument(help="Local directory or Git URL.")] = ".",
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit the plan as JSON.")
    ] = False,
    variant: Annotated[
        str | None, typer.Option(help="Setup path, e.g. native or docker.")
    ] = None,
    component: Annotated[
        str | None, typer.Option(help="Component id in a multi-component repo.")
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(help="Which run command to use when README and manifests differ."),
    ] = None,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Ignore the cached analysis.")
    ] = False,
    verbose: VerboseOpt = False,
) -> None:
    """Show the plan for TARGET. Nothing is executed."""
    try:
        ui.stage(1, 3, "Resolving target")
        source = resolve(target)
        ui.stage(2, 3, "Analyzing")
        analysis = analyze(source.root, fresh=fresh)
        ui.stage(3, 3, "Probing this host (read-only)")
        host = probe_host()
    except (SourceError, NotADirectoryError) as exc:
        ui.error(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc

    result = _build_answered(
        source, analysis, host, Choices(variant=variant, component=component, run=run)
    )

    saved = write_plan(source.root, result)
    if json_out:
        sys.stdout.buffer.write(plan_json(result).encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        if verbose:
            _render_verbose(analysis, host)
        render_plan(result, _console())
    ui.done(
        f"Plan saved to {saved}. Nothing was executed."
        if saved
        else "Plan not saved (directory not writable). Nothing was executed."
    )


@app.command()
def doctor(
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit the host profile as JSON.")
    ] = False,
) -> None:
    """Probe the host: OS, shells, installed tools (read-only)."""
    profile = probe_host()
    if json_out:
        text = profile.model_dump_json(indent=2) + "\n"
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        ui.render_host(
            profile, Console(width=max(100, shutil.get_terminal_size().columns))
        )


@app.command()
def schema(
    markdown: Annotated[
        bool,
        typer.Option(
            "--markdown", help="Print the schema as Markdown (docs/schema.md)."
        ),
    ] = False,
) -> None:
    """Print the analysis JSON Schema."""
    # Bytes + LF so output is identical on every OS (the committed file is LF).
    if markdown:
        text = render_markdown()
    else:
        text = json.dumps(Analysis.model_json_schema(), indent=2) + "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


@app.command()
def config() -> None:
    """Show config, cache, and state paths."""
    _stub("config")


@export_app.command("devcontainer")
def export_devcontainer(
    target: Annotated[str, typer.Argument(help="Local directory or Git URL.")] = ".",
    write: Annotated[
        bool,
        typer.Option(
            "--write",
            help="Also write .devcontainer/devcontainer.json in the repo (asks first; never overwrites).",
        ),
    ] = False,
    variant: Annotated[
        str | None, typer.Option(help="Setup path, e.g. native or docker.")
    ] = None,
    component: Annotated[
        str | None, typer.Option(help="Component id in a multi-component repo.")
    ] = None,
    run: Annotated[
        str | None,
        typer.Option(help="Which run command to use when README and manifests differ."),
    ] = None,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Ignore the cached analysis.")
    ] = False,
) -> None:
    """Print a devcontainer.json for TARGET and save it to .rtfmup/. Runs nothing."""
    try:
        ui.stage(1, 3, "Resolving target")
        source = resolve(target)
        ui.stage(2, 3, "Analyzing")
        analysis = analyze(source.root, fresh=fresh)
    except (SourceError, NotADirectoryError) as exc:
        ui.error(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc
    ui.stage(3, 3, "Planning for a Linux container")
    result = _build_answered(
        source,
        analysis,
        linux_host(),
        Choices(variant=variant, component=component, run=run),
    )
    text = json.dumps(build_devcontainer(analysis, result, root=source.root), indent=2)
    text += "\n"

    saved = write_state_file(source.root, DEVCONTAINER_FILE, text)
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()
    ui.done(f"Saved to {saved}." if saved else "Not saved (directory not writable).")

    if write:
        target_file = source.root / ".devcontainer" / "devcontainer.json"
        legacy = source.root / ".devcontainer.json"
        existing = next((p for p in (target_file, legacy) if p.exists()), None)
        if existing is not None:
            ui.error(f"{existing} already exists; refusing to overwrite it.")
            raise typer.Exit(EXIT_USAGE)
        if not _is_interactive():
            ui.error("--write asks for confirmation and needs a terminal.")
            raise typer.Exit(EXIT_USAGE)
        if not typer.confirm(f"Create {target_file}?", default=False, err=True):
            ui.done("Not written.")
            raise typer.Exit(EXIT_DECLINED)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        # Same depth as .rtfmup/, so the relative compose path stays valid.
        target_file.write_text(text, encoding="utf-8", newline="\n")
        ui.done(f"Wrote {target_file}.")


@recipe_app.command("show")
def recipe_show(target: TargetArg) -> None:
    """Show the recorded recipe for TARGET."""
    _stub("recipe show")


@app.command("scan")
def scan_cmd(
    target: Annotated[str, typer.Argument(help="Local directory or Git URL.")] = ".",
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit the analysis as JSON.")
    ] = False,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Ignore the cached analysis.")
    ] = False,
) -> None:
    """Analyze TARGET (read-only) and show what rtfmup found."""
    try:
        source = resolve(target)
        analysis = analyze(source.root, fresh=fresh)
    except (SourceError, NotADirectoryError) as exc:
        ui.error(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc
    if json_out:
        # Bytes + LF so the output is identical on every OS.
        text = analysis.model_dump_json(indent=2) + "\n"
        sys.stdout.buffer.write(text.encode("utf-8"))
        sys.stdout.buffer.flush()
    else:
        ui.render_summary(
            analysis, Console(width=max(100, shutil.get_terminal_size().columns))
        )


def _known_names() -> set[str]:
    # typer 0.28 vendors click privately, so duck-type the group.
    group = typer.main.get_command(app)
    return set(getattr(group, "commands", {})) | {"--help", "-h", "--version"}


def shim(argv: list[str]) -> list[str]:
    """D16: `rtfmup <target>` means `rtfmup up <target>`."""
    if argv and argv[0] not in _known_names():
        return ["up", *argv]
    return argv


def main(argv: list[str] | None = None) -> int:
    args = shim(list(sys.argv[1:] if argv is None else argv))
    try:
        app(args=args, prog_name="rtfmup")
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
