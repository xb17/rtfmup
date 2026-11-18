"""`up` orchestration (T3.6): confirm, execute the plan, health-check the run step.

The caller has already resolved, analyzed and planned. Everything here is about
running that plan safely: nothing starts before confirmation, high-risk steps ask
one by one, and every step's outcome lands in `.rtfmup/state.json`.
"""

from __future__ import annotations

import hashlib
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
from rtfmup.execute.unixpath import refreshed_path as unix_refreshed_path
from rtfmup.execute.winpath import refreshed_path
from rtfmup.exitcodes import (
    EXIT_DECLINED,
    EXIT_GAPS_BLOCK,
    EXIT_OK,
    EXIT_STEP_FAILED,
    EXIT_USAGE,
)
from rtfmup.models import Analysis
from rtfmup.plan.build import analysis_ref
from rtfmup.plan.model import Gap, Plan, Step
from rtfmup.plan.store import write_state_file

STATE_FILE = "state.json"
RUN_LOG = "run.log"

# Without a runtime there is nothing to run the project with, and a `cd` we cannot
# resolve would run later steps in the wrong place. Other gaps are shown and
# confirmed over, not blocked on.
BLOCKING_GAPS = ("missing_runtime", "runtime_version", "working_directory")

StepState = Literal["pending", "ok", "failed", "skipped", "cancelled", "not_run"]
Outcome = Literal[
    "up",
    "stopped",
    "declined",
    "blocked",
    "failed",
    "cannot_resume",
    "nothing",
]


@dataclass
class UpResult:
    outcome: Outcome
    message: str = ""
    steps: dict[str, str] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return {
            "up": EXIT_OK,
            "stopped": EXIT_OK,
            "declined": EXIT_DECLINED,
            "blocked": EXIT_GAPS_BLOCK,
            "failed": EXIT_STEP_FAILED,
            "nothing": EXIT_STEP_FAILED,
            "cannot_resume": EXIT_USAGE,
        }[self.outcome]


def plan_hash(plan: Plan, analysis: Analysis) -> str:
    """sha256 of the plan's JSON (T4.2). It covers the analysis, the host, variant,
    component and every step, so any of them changing invalidates a resume.

    The analysis is hashed without `git_dirty`: the steps of the first run (build
    output, `.env`) make a git checkout dirty, and that alone must not refuse the
    resume. A real README or manifest change still alters the analysis and the hash.
    """
    stable = analysis_ref(analysis.model_copy(update={"git_dirty": None}))
    data = plan.model_copy(update={"analysis_ref": stable}).model_dump_json()
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def load_state(root: Path) -> dict | None:
    try:
        data = json.loads((root / CACHE_DIR / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def blocking_gaps(plan: Plan) -> list[Gap]:
    return [g for g in plan.gaps if g.kind in BLOCKING_GAPS]


class _State:
    """`.rtfmup/state.json`: per-step status, rewritten after every change."""

    def __init__(
        self,
        root: Path,
        plan: Plan,
        analysis: Analysis,
        done: dict[str, dict] | None = None,
    ) -> None:
        self.root = root
        done = done or {}
        self.data: dict = {
            "schema_version": "1",
            "analysis_ref": plan.analysis_ref,
            "plan_hash": plan_hash(plan, analysis),
            "started": _now(),
            "outcome": None,
            "steps": {
                s.id: done.get(s.id)
                or {"title": s.title, "kind": s.kind, "status": "pending"}
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


QUIET_TAIL = 40  # lines of a failing step's output that --quiet still shows


def _failed(err: IO[str], tail: list[str], quiet: bool) -> None:
    """After a failure line: (quiet only) the output we held back, then the
    diagnosis, what probably went wrong and how to fix it (T4.1)."""
    if quiet and tail:
        _say(err, f"last {min(len(tail), QUIET_TAIL)} lines of output:")
        for line in tail[-QUIET_TAIL:]:
            _say(err, f"  {line}")
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
    resume: bool = False,
    quiet: bool = False,
) -> UpResult:
    out = out or sys.stdout
    err = err or sys.stderr
    root = root.resolve()

    done_before: dict[str, dict] = {}
    if resume:
        prior = load_state(root)
        if prior is None:
            return UpResult(
                "cannot_resume",
                "there is no earlier run to resume: run `rtfmup up` without --resume.",
            )
        if prior.get("plan_hash") != plan_hash(plan, analysis):
            return UpResult(
                "cannot_resume",
                "the plan changed since the last run: run `rtfmup up` without "
                "--resume.",
            )
        # The run step and anything after it always run again.
        done_before = {
            sid: v
            for sid, v in prior.get("steps", {}).items()
            if v.get("status") == "ok" and v.get("kind") not in ("run", "healthcheck")
        }
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
    to_run = [
        s
        for s in plan.steps
        if is_runnable(s) and s.kind != "healthcheck" and s.id not in done_before
    ]
    env_steps = [
        s
        for s in plan.steps
        if s.kind == "env" and s.platform_ok and s.id not in done_before
    ]
    listed_only = [
        s
        for s in plan.steps
        if s.kind not in ("env", "healthcheck") and not is_runnable(s)
    ]
    for s in listed_only:
        _say(err, f"not run by rtfmup: {s.title}")

    confirmer = confirmer or Confirmer(assume_yes=assume_yes, out=err)
    if not to_run and not env_steps:
        if done_before:
            return UpResult("stopped", "Every step in the plan is already done.")
        if listed_only:
            # Every step is for another OS or manual: the plan cannot run here.
            return UpResult(
                "blocked", "Every step in the plan needs a different OS or is manual."
            )
        return UpResult("nothing", "Nothing to run.")
    if not confirmer.confirm_batch(to_run):
        return UpResult("declined", "Nothing was run.")

    state = _State(root, plan, analysis, done_before)
    log = RunLog(root / CACHE_DIR / RUN_LOG)
    prompter = prompter or Prompter(interactive=interactive and not assume_yes, out=err)
    cancel = cancel or threading.Event()
    total = len(to_run) + len(env_steps) + len(done_before)
    child_env: dict[str, str] = {}  # grows when a runtime step changes PATH
    installed_runtimes: set[str] = set()

    def done(outcome: Outcome, message: str = "") -> UpResult:
        state.finish(outcome)
        return UpResult(outcome, message, state.statuses())

    index = 0
    for step in plan.steps:
        if step.kind == "healthcheck":
            continue
        if step.id in done_before:
            index += 1
            _say(err, f"[{index}/{total}] {step.kind} · {step.title}: already done")
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
                quiet=quiet,
            )
            if health is not None and not health.up:
                state.set(step.id, "failed", exit_code=res.exit_code)
                if res.status in ("failed", "error"):
                    _failed(err, res.tail, quiet)
                return done("failed", health.reason)
            if res.status == "cancelled":
                state.set(step.id, "cancelled")
                return done("up" if health and health.up else "stopped")
            state.set(step.id, "ok" if res.ok else "failed", exit_code=res.exit_code)
            if not res.ok:
                if res.status in ("failed", "error"):
                    _failed(err, res.tail, quiet)
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
            quiet=quiet,
        )
        state.set(
            step.id,
            "ok" if res.ok else res.status,
            exit_code=res.exit_code,
            seconds=round(res.duration, 1),
        )
        if not res.ok:
            if res.status in ("failed", "error"):
                _failed(err, res.tail, quiet)
            return done("failed", f"{step.title}: {res.status}.")
        if step.kind == "runtime":
            current = child_env.get("PATH", os.environ.get("PATH", ""))
            if plan.host_os == "windows":
                child_env["PATH"] = refreshed_path(current)
            else:
                # Provider step ids are `runtime-<name>-<provider>` (providers/base.py).
                installed_runtimes.add(step.id.split("-")[1] if "-" in step.id else "")
                child_env["PATH"] = unix_refreshed_path(
                    current, installed=frozenset(installed_runtimes)
                )

    return done("stopped", "Setup finished; the plan has no run step.")
