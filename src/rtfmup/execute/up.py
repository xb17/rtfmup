"""`up` orchestration (T3.6): confirm, execute the plan, health-check the run step.

The caller has already resolved, analyzed and planned. Everything here is about
running that plan safely: nothing starts before confirmation, high-risk steps ask
one by one, and every step's outcome lands in `.rtfmup/state.json`.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Literal

from rtfmup.analyze import CACHE_DIR
from rtfmup.execute.confirm import Confirmer, is_runnable
from rtfmup.execute.diagnose import diagnose
from rtfmup.execute.envsetup import EnvResult, Prompter, setup_env
from rtfmup.execute.health import DEFAULT_HEALTH_TIMEOUT, run_with_health
from rtfmup.execute.runner import DEFAULT_TIMEOUT, RunLog, run_step
from rtfmup.execute.winpath import refreshed_path
from rtfmup.models import Analysis
from rtfmup.plan.model import Gap, Plan, Step
from rtfmup.plan.store import write_state_file

STATE_FILE = "state.json"
RUN_LOG = "run.log"

# Without a runtime there is nothing to run the project with, and a `cd` we cannot
# resolve would run later steps in the wrong place. Other gaps are shown and
# confirmed over, not blocked on.
BLOCKING_GAPS = ("missing_runtime", "runtime_version", "working_directory")

StepState = Literal["pending", "ok", "failed", "skipped", "cancelled", "not_run"]
Outcome = Literal["up", "stopped", "declined", "blocked", "failed", "unsupported"]


@dataclass
class UpResult:
    outcome: Outcome
    message: str = ""
    steps: dict[str, str] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return {
            "up": 0,
            "stopped": 0,
            "declined": 3,
            "blocked": 1,
            "failed": 1,
            "unsupported": 2,
        }[self.outcome]


def blocking_gaps(plan: Plan) -> list[Gap]:
    return [g for g in plan.gaps if g.kind in BLOCKING_GAPS]


class _State:
    """`.rtfmup/state.json`: per-step status, rewritten after every change."""

    def __init__(self, root: Path, plan: Plan) -> None:
        self.root = root
        self.data: dict = {
            "schema_version": "1",
            "analysis_ref": plan.analysis_ref,
            "started": _now(),
            "outcome": None,
            "steps": {
                s.id: {"title": s.title, "kind": s.kind, "status": "pending"}
                for s in plan.steps
            },
        }
        self.save()

    def set(self, step_id: str, status: str, **extra: object) -> None:
        self.data["steps"][step_id].update(status=status, **extra)
        self.save()

    def finish(self, outcome: str) -> None:
        self.data.update(outcome=outcome, finished=_now())
        self.save()

    def save(self) -> None:
        write_state_file(self.root, STATE_FILE, json.dumps(self.data, indent=2) + "\n")

    def statuses(self) -> dict[str, str]:
        return {k: v["status"] for k, v in self.data["steps"].items()}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _say(err: IO[str], text: str = "") -> None:
    err.write(text + "\n")
    err.flush()


def _diagnose(err: IO[str], tail: list[str]) -> None:
    """After a failure line: what probably went wrong and how to fix it (T4.1)."""
    for d in diagnose(tail):
        _say(err)
        _say(err, f"Diagnosis: {d.explanation}")
        _say(err, f"  fix: {d.fix}")


def _env_step(
    step: Step,
    root: Path,
    analysis: Analysis,
    prompter: Prompter,
    err: IO[str],
) -> EnvResult:
    template = next((e.file for e in step.evidence), None)
    if template is None:
        return EnvResult(skipped="no template recorded for this step")
    return setup_env(root, template, analysis.env, prompter, cwd=step.cwd)


def execute_plan(
    plan: Plan,
    analysis: Analysis,
    root: Path,
    *,
    assume_yes: bool = False,
    interactive: bool = True,
    health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
    step_timeout: float | None = DEFAULT_TIMEOUT,
    confirmer: Confirmer | None = None,
    prompter: Prompter | None = None,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    cancel: threading.Event | None = None,
) -> UpResult:
    out = out or sys.stdout
    err = err or sys.stderr
    root = root.resolve()

    if plan.host_os != "windows":
        return UpResult(
            "unsupported",
            f"`up` is not yet supported on this OS ({plan.host_os}).",
        )
    stuck = blocking_gaps(plan)
    if stuck:
        for g in stuck:
            _say(err, f"blocked: {g.message}")
            if g.fix:
                _say(err, f"  fix: {g.fix}")
        return UpResult(
            "blocked", "Resolve the gaps above, then run `rtfmup up` again."
        )

    # Steps we execute here. Env is interactive, health is the run step's job,
    # and manual or other-OS steps are only listed.
    to_run = [s for s in plan.steps if is_runnable(s) and s.kind != "healthcheck"]
    env_steps = [s for s in plan.steps if s.kind == "env" and s.platform_ok]
    listed_only = [
        s
        for s in plan.steps
        if s.kind not in ("env", "healthcheck") and not is_runnable(s)
    ]
    for s in listed_only:
        _say(err, f"not run by rtfmup: {s.title}")

    confirmer = confirmer or Confirmer(assume_yes=assume_yes, out=err)
    if not to_run and not env_steps:
        return UpResult("blocked", "Nothing to run.")
    if not confirmer.confirm_batch(to_run):
        return UpResult("declined", "Nothing was run.")

    state = _State(root, plan)
    log = RunLog(root / CACHE_DIR / RUN_LOG)
    prompter = prompter or Prompter(interactive=interactive and not assume_yes, out=err)
    cancel = cancel or threading.Event()
    total = len(to_run) + len(env_steps)
    child_env: dict[str, str] = {}  # grows when a runtime step changes PATH

    def done(outcome: Outcome, message: str = "") -> UpResult:
        state.finish(outcome)
        return UpResult(outcome, message, state.statuses())

    index = 0
    for step in plan.steps:
        if step.kind == "healthcheck":
            continue
        if step.kind == "env" and step.platform_ok:
            index += 1
            _say(err, f"[{index}/{total}] env · {step.title}")
            result = _env_step(step, root, analysis, prompter, err)
            if result.skipped:
                _say(err, f"env setup skipped: {result.skipped}")
                state.set(step.id, "skipped", reason=result.skipped)
            elif result.unfilled:
                names = ", ".join(result.unfilled)
                _say(err, f"required values still empty in .env: {names}")
                state.set(step.id, "failed", unfilled=result.unfilled)
                return done("failed", f"Fill in {names} in .env, then run again.")
            else:
                state.set(
                    step.id,
                    "ok",
                    created=result.created,
                    filled=result.filled,
                    appended=result.appended,
                )
            continue
        if step not in to_run:
            state.set(step.id, "not_run")
            continue

        index += 1
        if step.risk == "high" and not confirmer.confirm_high_risk(step):
            state.set(step.id, "skipped", reason="high risk, not approved")
            if step.kind == "run":
                # Setup ran, but the project was never started: not a success.
                return done(
                    "declined",
                    f"Setup finished, but the run step was not approved: {step.title}",
                )
            continue

        if step.kind == "run":
            res, health = run_with_health(
                step,
                root,
                plan.health,
                health_timeout=health_timeout,
                index=index,
                total=total,
                env=child_env,
                log=log,
                out=out,
                err=err,
                cancel=cancel,
            )
            if health is not None and not health.up:
                state.set(step.id, "failed", exit_code=res.exit_code)
                if res.status in ("failed", "error"):
                    _diagnose(err, res.tail)
                return done("failed", health.reason)
            if res.status == "cancelled":
                state.set(step.id, "cancelled")
                return done("up" if health and health.up else "stopped")
            state.set(step.id, "ok" if res.ok else "failed", exit_code=res.exit_code)
            if not res.ok:
                if res.status in ("failed", "error"):
                    _diagnose(err, res.tail)
                return done("failed", f"{step.title} ended with {res.status}.")
            return done("up" if health and health.up else "stopped")

        res = run_step(
            step,
            root,
            index=index,
            total=total,
            timeout=step_timeout,
            env=child_env,
            log=log,
            out=out,
            err=err,
            cancel=cancel,
        )
        state.set(
            step.id,
            "ok" if res.ok else res.status,
            exit_code=res.exit_code,
            seconds=round(res.duration, 1),
        )
        if not res.ok:
            if res.status in ("failed", "error"):
                _diagnose(err, res.tail)
            return done("failed", f"{step.title}: {res.status}.")
        if step.kind == "runtime":
            child_env["PATH"] = refreshed_path(
                child_env.get("PATH", os.environ.get("PATH", ""))
            )

    return done("stopped", "Setup finished; the plan has no run step.")
