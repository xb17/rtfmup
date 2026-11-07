"""Terminal output. Errors go to stderr, results to stdout.

The summary is plain ASCII-safe rich output so it also works when stdout is a
pipe on a legacy Windows code page.
"""

from __future__ import annotations

from collections import Counter

import typer
from rich.console import Console
from rich.table import Table

from rtfmup.models import Analysis, SourceRef

CATEGORY_ORDER = ("install", "setup", "build", "run", "test", "lint", "other")


def error(message: str) -> None:
    typer.echo(f"rtfmup: error: {message}", err=True)


def not_implemented(command: str) -> None:
    typer.echo(f"rtfmup: '{command}' is not implemented yet", err=True)


def where(ev: list[SourceRef]) -> str:
    """`README.md:12` for the first piece of evidence."""
    if not ev:
        return ""
    first = ev[0]
    return f"{first.file}:{first.line_start}" if first.line_start else first.file


def _table(console: Console, title: str, *columns: str) -> Table:
    table = Table(
        title=title, title_justify="left", show_lines=False, box=None, pad_edge=False
    )
    for col in columns:
        table.add_column(col, overflow="fold")
    return table


def render_summary(a: Analysis, console: Console) -> None:
    """Human-readable summary of an Analysis."""
    p = console.print
    head = (
        f"{a.git_head[:7]} ({'dirty' if a.git_dirty else 'clean'})"
        if a.git_head
        else "not a git repo"
    )
    p(f"[bold]rtfmup scan[/bold] {a.root}")
    readme = f"{a.readme} ({a.readme_format})" if a.readme else "none found"
    p(f"README: {readme}    git: {head}")
    flags = [
        n
        for n, on in (("docker", a.has_docker), ("devcontainer", a.has_devcontainer))
        if on
    ]
    if flags:
        p(f"Also present: {', '.join(flags)}")

    p()
    t = _table(console, "Components", "id", "runtimes", "package managers", "runnable")
    for c in a.components:
        t.add_row(
            c.id,
            ", ".join(c.runtimes) or "-",
            ", ".join(c.package_managers) or "-",
            "yes" if c.runnable else "no",
        )
    console.print(t)

    if a.runtimes:
        p()
        t = _table(console, "Runtimes", "runtime", "version", "source", "confidence")
        for r in a.runtimes:
            t.add_row(r.name, r.version_spec or "-", where(r.evidence), r.confidence)
        console.print(t)

    if a.package_managers:
        p()
        t = _table(console, "Package managers", "manager", "lockfile", "component")
        for m in a.package_managers:
            t.add_row(m.name, m.lockfile or "(none)", m.component)
        console.print(t)

    if a.env:
        p()
        t = _table(
            console,
            "Environment variables",
            "name",
            "required",
            "secret",
            "description",
            "source",
        )
        for e in sorted(
            a.env,
            key=lambda v: (v.required != "required", v.required != "unknown", v.name),
        ):
            style = (
                "bold red"
                if e.required == "required"
                else ("yellow" if e.required == "unknown" else "")
            )
            t.add_row(
                e.name,
                e.required,
                "yes" if e.secret_like else "",
                e.description or "",
                where(e.evidence),
                style=style,
            )
        console.print(t)

    if a.services:
        p()
        t = _table(console, "Services", "service", "image", "ports", "source")
        for s in a.services:
            t.add_row(
                s.name,
                s.image or "-",
                ", ".join(map(str, s.ports)) or "-",
                where(s.evidence),
            )
        console.print(t)

    if a.external_accounts:
        p()
        t = _table(console, "External accounts", "name", "url", "env vars")
        for x in a.external_accounts:
            t.add_row(x.name, x.url, ", ".join(x.related_env) or "-")
        console.print(t)

    if a.ports:
        p()
        p(
            "Ports: "
            + ", ".join(
                f"{x.port} ({x.url})" if x.url else str(x.port) for x in a.ports
            )
        )

    if a.deployments:
        p()
        t = _table(
            console,
            "Deployment files (detected, never acted on)",
            "kind",
            "file",
            "summary",
        )
        for d in a.deployments:
            t.add_row(d.kind, d.file, d.summary or "")
        console.print(t)

    p()
    counts = Counter(c.category for c in a.commands)
    shown = [f"{cat} {counts[cat]}" for cat in CATEGORY_ORDER if counts[cat]]
    p(f"Commands: {len(a.commands)}" + (f"  ({', '.join(shown)})" if shown else ""))
    risky = [c for c in a.commands if {"pipe_to_shell", "sudo"} & set(c.flags)]
    for c in risky:
        p(
            f"  [yellow]{'/'.join(f for f in c.flags if f in ('pipe_to_shell', 'sudo'))}[/yellow] {c.text}  ({where(c.evidence)})",
            markup=True,
            highlight=False,
        )

    if a.conflicts:
        p()
        p("[bold red]Conflicts[/bold red] (manifests win over the README)")
        for c in a.conflicts:
            p(f"  - {c.message}  ({where(c.evidence)})", markup=False, highlight=False)

    if a.ambiguities:
        p()
        p("[bold yellow]Questions[/bold yellow] (rtfmup will ask rather than guess)")
        for amb in a.ambiguities:
            p(
                f"  - {amb.question}  [{' | '.join(amb.options)}]",
                markup=False,
                highlight=False,
            )

    if a.warnings:
        p()
        p("Warnings")
        for w in a.warnings:
            p(f"  - {w}", markup=False, highlight=False)
