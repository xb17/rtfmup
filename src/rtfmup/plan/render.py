"""Human-readable plan. ASCII-safe rich output (works on a legacy Windows code page)."""

from __future__ import annotations

import shlex

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from rtfmup.plan.model import Gap, Plan, Step

RISK_STYLE = {"low": "green", "medium": "yellow", "high": "bold red"}
GAP_TITLES = {
    "missing_runtime": "Missing tools",
    "runtime_version": "Wrong versions",
    "missing_env": "Configuration to provide",
    "external_account": "Accounts and keys",
    "service": "Services",
    "unsupported_platform": "Steps for another OS (listed, not run)",
    "working_directory": "Directory not found",
    "unresolved": "Unresolved",
}


def command_text(step: Step) -> str:
    if step.argv:
        return shlex.join(step.argv)
    return step.shell_text or step.title


def _where(step: Step) -> str:
    if not step.evidence:
        return ""
    ev = step.evidence[0]
    return f"{ev.file}:{ev.line_start}" if ev.line_start else ev.file


def _step_table(plan: Plan) -> Table:
    table = Table(
        title="Steps (nothing has been run)",
        title_justify="left",
        box=None,
        pad_edge=False,
    )
    for col in ("#", "kind", "risk", "dir", "command", "source"):
        table.add_column(col, overflow="fold")
    for i, s in enumerate(plan.steps, 1):
        style = "dim" if not s.platform_ok else ""
        env = "".join(f"{k}={v} " for k, v in s.env.items())
        text = f"{env}{command_text(s)}"
        if s.kind in ("env", "healthcheck"):
            text = s.title
        table.add_row(
            str(i),
            s.kind,
            f"[{RISK_STYLE[s.risk]}]{s.risk}[/]",
            escape(s.cwd),
            escape(text),
            escape(_where(s)),
            style=style,
        )
    return table


def _risk_notes(plan: Plan) -> list[str]:
    return [
        f"  step {i} ({s.risk}): {'; '.join(s.risk_reasons)}"
        for i, s in enumerate(plan.steps, 1)
        if s.risk != "low"
    ]


def render_plan(plan: Plan, console: Console) -> None:
    p = console.print
    bits = [f"{plan.host_os} {plan.host_arch}", f"component {plan.component}"]
    if plan.variant:
        bits.append(f"variant {plan.variant}")
    p(f"[bold]rtfmup plan[/bold] ({', '.join(bits)})")
    p()
    if plan.steps:
        console.print(_step_table(plan))
        notes = _risk_notes(plan)
        if notes:
            p()
            p("Why some steps are not low risk")
            for n in notes:
                p(n, markup=False, highlight=False)
    else:
        p("No steps: nothing to install, configure or run was found.")
    _render_alternatives(plan, console)
    if plan.health:
        p()
        p(f"Health check: {plan.health.url or 'port ' + str(plan.health.port)}")
    _render_gaps(plan.gaps, console)


def _render_alternatives(plan: Plan, console: Console) -> None:
    if not plan.skipped_alternatives:
        return
    console.print()
    console.print(
        "[bold]Alternatives not chosen[/bold] (the README offers these instead)"
    )
    for alt in plan.skipped_alternatives:
        ev = alt.evidence[0] if alt.evidence else None
        where = ""
        if ev is not None:
            where = f"{ev.file}:{ev.line_start}" if ev.line_start else ev.file
        console.print(f"  - {alt.text}  ({where})", markup=False, highlight=False)
        console.print(f"      {alt.reason}", markup=False, highlight=False)


def _render_gaps(gaps: list[Gap], console: Console) -> None:
    if not gaps:
        return
    console.print()
    console.print("[bold]Gaps[/bold] (rtfmup will not guess these)")
    last = None
    for g in gaps:
        if g.kind != last:
            console.print(f"  [bold]{GAP_TITLES[g.kind]}[/bold]")
            last = g.kind
        console.print(f"    - {g.message}", markup=False, highlight=False)
        if g.fix:
            console.print(f"      fix: {g.fix}", markup=False, highlight=False)
