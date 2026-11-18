"""Process exit codes (T4.3). The one place they are defined.

The README table is checked against `TABLE` by a test.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_USAGE = 2
EXIT_DECLINED = 3
EXIT_GAPS_BLOCK = 4

# (code, name, meaning), in the order the README lists them.
TABLE: tuple[tuple[int, str, str], ...] = (
    (EXIT_OK, "ok", "Finished: the project is up, or setup completed."),
    (EXIT_STEP_FAILED, "step failed", "A step failed, or nothing could be run."),
    (
        EXIT_USAGE,
        "usage",
        ("Bad arguments, an unanswered question, or `--resume` refused."),
    ),
    (EXIT_DECLINED, "declined", "You declined the confirmation, or the run step."),
    (
        EXIT_GAPS_BLOCK,
        "gaps block",
        (
            "A gap makes the plan impossible (a missing runtime with no installer, "
            "steps for another OS only, a directory that is not there). Nothing was run."
        ),
    ),
)
