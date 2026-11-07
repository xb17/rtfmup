"""Command-line entry point (typer)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from rtfmup import __version__, ui
from rtfmup.scan import format_summary, scan

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
def doctor() -> None:
    """Probe the host: OS, shells, installed tools."""
    _stub("doctor")


@app.command()
def schema() -> None:
    """Print the analysis JSON Schema."""
    _stub("schema")


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
    path: Annotated[Path, typer.Argument(help="Repo directory.")] = Path("."),
    json_out: Annotated[
        bool, typer.Option("--json", help="Emit JSON instead of a summary.")
    ] = False,
) -> None:
    """Detect manifests in a local repo."""
    try:
        result = scan(path)
    except NotADirectoryError as exc:
        ui.error(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc
    if json_out:
        typer.echo(json.dumps(result.to_dict(), indent=2))
    else:
        typer.echo(format_summary(result))


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
