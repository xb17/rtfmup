"""Confirmation flow (T3.2, D8).

- One confirmation covers every low and medium risk step. (D8 words it "all
  low-risk steps" but `--yes` approves "low and medium", so the batch matches
  that. Medium steps are listed with their risk so nothing is hidden.)
- High-risk steps ask individually, show the exact command, and need `yes` typed
  in full. `y` is not enough.
- `--yes` approves the batch only. High-risk steps are skipped and reported,
  never auto-approved.
- EOF on stdin (no terminal, CI) is a refusal, never an approval.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import IO

from rtfmup.execute.shells import display_command
from rtfmup.plan.model import Step


def is_runnable(step: Step) -> bool:
    """A step we can actually execute here (not manual, not another OS)."""
    return step.platform_ok and bool(step.argv or step.shell_text)


def split_by_risk(steps: Sequence[Step]) -> tuple[list[Step], list[Step]]:
    """(batch, high): runnable low/medium steps, and runnable high-risk steps."""
    runnable = [s for s in steps if is_runnable(s)]
    return (
        [s for s in runnable if s.risk != "high"],
        [s for s in runnable if s.risk == "high"],
    )


class Confirmer:
    def __init__(
        self,
        *,
        assume_yes: bool = False,
        read: Callable[[str], str] = input,
        out: IO[str] | None = None,
    ) -> None:
        self.assume_yes = assume_yes
        self._read = read
        self._out = out

    def _say(self, text: str = "") -> None:
        out = self._out or sys.stderr
        out.write(text + "\n")
        out.flush()

    def _ask(self, prompt: str) -> str | None:
        """The answer, or None on EOF / Ctrl+C (both count as a refusal)."""
        try:
            return self._read(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            self._say()
            return None

    def confirm_batch(self, steps: Sequence[Step]) -> bool:
        """One question for all low and medium risk steps."""
        batch, _ = split_by_risk(steps)
        if not batch:
            return True
        self._say(f"{len(batch)} step(s) will run on this machine:")
        for i, s in enumerate(batch, 1):
            tag = (
                ""
                if s.risk == "low"
                else f"  [{s.risk} risk: {'; '.join(s.risk_reasons)}]"
            )
            self._say(f"  {i}. {display_command(s)}{tag}")
        if self.assume_yes:
            self._say("--yes: approved.")
            return True
        return self._ask("Run these steps? [y/N] ") in ("y", "yes")

    def confirm_high_risk(self, step: Step) -> bool:
        """Per-step. Shows the exact command; requires the word `yes`."""
        self._say(f"HIGH RISK step: {step.title}")
        self._say(f"  command: {display_command(step)}")
        if step.cwd not in ("", "."):
            self._say(f"  cwd:     {step.cwd}")
        for reason in step.risk_reasons:
            self._say(f"  why:     {reason}")
        if self.assume_yes:
            self._say("  skipped: --yes never approves high-risk steps.")
            return False
        if self._ask("Type 'yes' to run this step, anything else skips it: ") == "yes":
            return True
        self._say("  skipped.")
        return False
