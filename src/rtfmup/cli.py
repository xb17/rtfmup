"""Command-line entry point (typer)."""

from __future__ import annotations

import json
import shutil
import sys
from typing import Annotated

import typer
from rich.console import Console

from rtfmup import __version__, ui
from rtfmup.analyze import analyze
from rtfmup.host.probe import probe_host
from rtfmup.models import Analysis
from rtfmup.source import SourceError, resolve

EXIT_USAGE = 2

app = typer.Typer(
    name="rtfmup",
    help="Set up any repo from its README. (Early development: only `scan` works.)",
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


@app.command()
def up(target: TargetArg) -> None:
    """Analyze TARGET, show the plan, and set it up."""
    _stub("up")


@app.command()
def plan(target: TargetArg) -> None:
    """Show the plan for TARGET without executing anything."""
    _stub("plan")


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
def schema() -> None:
    """Print the analysis JSON Schema."""
    # Bytes + LF so output is identical on every OS (the committed file is LF).
    text = json.dumps(Analysis.model_json_schema(), indent=2) + "\n"
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


@app.command()
def config() -> None:
    """Show config, cache, and state paths."""
    _stub("config")


@export_app.command("devcontainer")
def export_devcontainer(target: TargetArg) -> None:
    """Write a devcontainer.json derived from the analysis."""
    _stub("export devcontainer")


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
