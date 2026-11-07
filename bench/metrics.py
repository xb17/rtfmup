"""Benchmark metrics (PLAN §4.2). Pure functions: no I/O, no network.

Definitions, from the plan:
- Commands are normalized (whitespace collapsed, prompt stripped, trailing `;`
  dropped, case-sensitive). A gold step is *matched* when an extracted command has
  the same normalized text and the same category.
- Pass 2 step recall   = matched gold steps / gold steps.
- Pass 2 precision     = extracted commands (install/setup/build/run) that match a
                         gold step / extracted commands in those categories.
- Category accuracy    = among gold steps whose text was extracted, the share with
                         the same category.
- Env var recall       = share of gold required vars found with required in
                         {required, unknown}.
Only README-sourced extracted commands count (CI and Procfile commands do not).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict

from rtfmup.models import Analysis

SCORED_CATEGORIES = ("install", "setup", "build", "run")
_PROMPT = re.compile(r"^(?:\$|>|PS>|PS [^>]*>)\s+")


class GoldStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["install", "setup", "build", "run", "test", "lint", "other"]
    command: str
    variant: Literal["docker", "native", "windows", "macos", "linux"] | None = None
    platform: Literal["any", "windows", "unix", "macos", "linux"] = "any"


class GoldRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    spec: str


class Gold(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    variant: Literal["docker", "native"]
    steps: list[GoldStep]
    runtimes: list[GoldRuntime]
    env_required: list[str]
    services: list[str]
    notes: str = ""


def normalize(command: str) -> str:
    text = _PROMPT.sub("", command.strip())
    text = " ".join(text.split())
    return text.removesuffix(";").rstrip()


def _ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


@dataclass
class RepoScore:
    name: str
    ecosystem: str
    held_out: bool
    seconds: float
    gold_steps: int
    matched_gold: int
    extracted: int  # README commands in the scored categories
    matched_extracted: int
    text_matched: int  # gold steps whose text was extracted (any category)
    category_correct: int
    env_gold: int
    env_found: int
    risk_flags: int  # extracted README commands flagged pipe_to_shell or sudo
    conflicts: int
    ambiguities: int
    missed: list[str] = field(default_factory=list)  # gold steps not matched
    extra: list[str] = field(
        default_factory=list
    )  # extracted, in scored categories, not gold

    @property
    def recall(self) -> float | None:
        return _ratio(self.matched_gold, self.gold_steps)

    @property
    def precision(self) -> float | None:
        return _ratio(self.matched_extracted, self.extracted)

    @property
    def category_accuracy(self) -> float | None:
        return _ratio(self.category_correct, self.text_matched)

    @property
    def env_recall(self) -> float | None:
        return _ratio(self.env_found, self.env_gold)


def score_repo(
    analysis: Analysis, gold: Gold, *, ecosystem: str, held_out: bool, seconds: float
) -> RepoScore:
    readme_cmds = [
        c for c in analysis.commands if c.evidence[0].detector.startswith("readme.")
    ]
    extracted = {(normalize(c.text), c.category) for c in readme_cmds}
    extracted_texts: dict[str, set[str]] = {}
    for text, cat in extracted:
        extracted_texts.setdefault(text, set()).add(cat)

    gold_set = {(normalize(s.command), s.category) for s in gold.steps}
    matched = gold_set & extracted
    text_matched = [g for g in gold_set if g[0] in extracted_texts]
    category_correct = [g for g in text_matched if g[1] in extracted_texts[g[0]]]

    scored = [c for c in readme_cmds if c.category in SCORED_CATEGORIES]
    matched_extracted = [
        c for c in scored if (normalize(c.text), c.category) in gold_set
    ]

    env = {e.name: e.required for e in analysis.env}
    env_found = [n for n in gold.env_required if env.get(n) in ("required", "unknown")]

    return RepoScore(
        name=gold.repo,
        ecosystem=ecosystem,
        held_out=held_out,
        seconds=seconds,
        gold_steps=len(gold_set),
        matched_gold=len(matched),
        extracted=len(scored),
        matched_extracted=len(matched_extracted),
        text_matched=len(text_matched),
        category_correct=len(category_correct),
        env_gold=len(gold.env_required),
        env_found=len(env_found),
        risk_flags=sum(
            1 for c in readme_cmds if {"pipe_to_shell", "sudo"} & set(c.flags)
        ),
        conflicts=len(analysis.conflicts),
        ambiguities=len(analysis.ambiguities),
        missed=sorted(f"{cat}: {text}" for text, cat in gold_set - extracted),
        extra=sorted(
            {
                f"{c.category}: {normalize(c.text)}"
                for c in scored
                if (normalize(c.text), c.category) not in gold_set
            }
        ),
    )


@dataclass
class Aggregate:
    label: str
    repos: int
    recall: float | None
    precision: float | None
    category_accuracy: float | None
    env_recall: float | None
    seconds: float


def aggregate(label: str, scores: list[RepoScore]) -> Aggregate:
    """Micro-average: totals are summed over repos, then divided."""

    def total(attr: str) -> int:
        return sum(getattr(s, attr) for s in scores)

    return Aggregate(
        label=label,
        repos=len(scores),
        recall=_ratio(total("matched_gold"), total("gold_steps")),
        precision=_ratio(total("matched_extracted"), total("extracted")),
        category_accuracy=_ratio(total("category_correct"), total("text_matched")),
        env_recall=_ratio(total("env_found"), total("env_gold")),
        seconds=sum(s.seconds for s in scores),
    )


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"
