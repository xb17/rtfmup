"""Per-OS command construction (D6, D17).

argv steps run directly. `needs_shell` steps run through `pwsh` on Windows and
`bash -c` elsewhere. The program path is resolved with `shutil.which` so Windows
`PATHEXT` shims (`npm.cmd`) work without `shell=True`.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

from rtfmup.plan.model import Step


class CommandError(Exception):
    """The step cannot be turned into a command on this host."""


def build_command(step: Step, *, path: str | None = None) -> list[str]:
    """`path` is the step's PATH. Windows' CreateProcess searches rtfmup's own PATH,
    not the child's, so a program added by a runtime step or a venv is resolved here."""
    if step.argv:
        argv = list(step.argv)
        resolved = shutil.which(argv[0], path=path)
        if resolved:
            argv[0] = resolved
        return argv
    if step.shell_text is not None:
        if sys.platform == "win32":
            pwsh = shutil.which("pwsh")
            if not pwsh:
                raise CommandError(
                    "PowerShell 7 (pwsh) is required for this step; "
                    "install it with: winget install --id Microsoft.PowerShell"
                )
            return [pwsh, "-NoProfile", "-NonInteractive", "-Command", step.shell_text]
        bash = shutil.which("bash")
        if not bash:
            raise CommandError("bash is required for this step but was not found")
        return [bash, "-c", step.shell_text]
    raise CommandError(f"step {step.id} has neither argv nor shell_text")


def display_command(step: Step) -> str:
    """The command as the user would read it (and as it goes in run.log)."""
    if step.argv:
        if sys.platform == "win32":
            return subprocess.list2cmdline(step.argv)
        return shlex.join(step.argv)
    return step.shell_text or ""
