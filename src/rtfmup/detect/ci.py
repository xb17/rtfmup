"""GitHub Actions workflows: `run:` steps become low-confidence Commands."""

from __future__ import annotations

import yaml

from rtfmup.detect._common import MAX_BYTES, MAX_FILES, Detection

SHELLS = {
    "bash": "bash",
    "sh": "sh",
    "pwsh": "powershell",
    "powershell": "powershell",
    "cmd": "cmd",
}


def _platform(runs_on: object) -> str:
    text = str(runs_on).lower()
    if "windows" in text:
        return "windows"
    if "macos" in text:
        return "macos"
    if "ubuntu" in text or "linux" in text:
        return "linux"
    return "any"


def logical_lines(script: str, cont: str = "\\") -> list[str]:
    """Split a `run:` block into logical commands, joining `cont` continuations."""
    out: list[str] = []
    current = ""
    for raw in script.splitlines():
        line = raw.rstrip()
        if not current and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith(cont):
            current += line[:-1].strip() + " "
            continue
        out.append((current + line.strip()).strip())
        current = ""
    if current.strip():
        out.append(current.strip())
    return out


def detect(d: Detection) -> None:
    wf_dir = d.root / ".github" / "workflows"
    if not wf_dir.is_dir() or wf_dir.is_symlink():
        return
    files = sorted(
        p
        for p in wf_dir.iterdir()
        if p.suffix in (".yml", ".yaml") and p.is_file() and not p.is_symlink()
    )
    if len(files) > MAX_FILES:
        d.warnings.append(
            f".github/workflows: {len(files)} files, only the first {MAX_FILES} read"
        )
        files = files[:MAX_FILES]
    for path in files:
        name = f".github/workflows/{path.name}"
        text = d.read(name)
        if text is None:
            continue
        if len(text) > MAX_BYTES:
            d.warnings.append(
                f"{d.relname(name)}: larger than {MAX_BYTES} bytes, skipped"
            )
            continue
        d.manifest(name, "github_workflow", "ci.github")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            d.warnings.append(
                f"{d.relname(name)}: invalid YAML ({str(exc).splitlines()[0]})"
            )
            continue
        _workflow(d, name, text, data)


def _workflow(d: Detection, name: str, text: str, data: object) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        return
    wf_name = str(data.get("name") or name)
    lines = text.splitlines()
    cursor = 0  # commands are located in file order, each search resumes here
    for job_id, job in data["jobs"].items():
        if not isinstance(job, dict):
            continue
        platform = _platform(job.get("runs-on", ""))
        defaults = job.get("defaults")
        run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
        default_shell = (
            run_defaults.get("shell") if isinstance(run_defaults, dict) else None
        )
        for step in job.get("steps") or []:
            if not isinstance(step, dict) or not isinstance(step.get("run"), str):
                continue
            shell_raw = step.get("shell") or default_shell
            shell = (
                SHELLS.get(str(shell_raw).split()[0], "unknown")
                if shell_raw
                else "unknown"
            )
            for cmd in logical_lines(
                step["run"], "`" if shell == "powershell" else "\\"
            ):
                needle = " ".join(cmd.split()[:2])
                line = None
                for i in range(cursor, len(lines)):
                    if needle in lines[i]:
                        line, cursor = i + 1, i + 1
                        break
                d.command(
                    cmd,
                    shell=shell,
                    category="other",
                    reason="ci:run step",
                    section_path=[wf_name, str(job_id)],
                    file=name,
                    line=line,
                    detector="ci.github",
                    platform=platform,
                )
