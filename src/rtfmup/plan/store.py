"""Where a plan is saved (D9): `<repo>/.rtfmup/plan.json`, excluded from git locally."""

from __future__ import annotations

import os
from pathlib import Path

from rtfmup.analyze import CACHE_DIR, exclude_state_dir
from rtfmup.plan.model import Plan

PLAN_FILE = "plan.json"


def plan_path(root: Path) -> Path:
    return root / CACHE_DIR / PLAN_FILE


def plan_json(plan: Plan) -> str:
    return plan.model_dump_json(indent=2) + "\n"


def write_plan(root: Path, plan: Plan) -> Path | None:
    """Save the plan; None if the directory is not writable (never fatal)."""
    target = plan_path(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        exclude_state_dir(root)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(plan_json(plan), encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    except OSError:
        return None
    return target
