"""Terminal output helpers. Errors go to stderr, results to stdout."""

from __future__ import annotations

import typer


def error(message: str) -> None:
    typer.echo(f"rtfmup: error: {message}", err=True)


def not_implemented(command: str) -> None:
    typer.echo(f"rtfmup: '{command}' is not implemented yet", err=True)
