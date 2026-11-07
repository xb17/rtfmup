"""Python manifests: pyproject.toml, requirements.txt, Pipfile, lockfiles."""

from __future__ import annotations

from rtfmup.detect._common import Detection, find_key_line


def detect(d: Detection) -> None:
    pyproject = _pyproject(d)

    if d.exists("requirements.txt"):
        d.manifest("requirements.txt", "requirements_txt", "python.requirements")
    if d.exists("Pipfile"):
        d.manifest("Pipfile", "pipfile", "python.pipfile")
    if d.exists("setup.py"):
        # Presence only. setup.py is never executed (PLAN §7.8).
        d.manifest("setup.py", "setup_py", "python.setup_py")

    # Package manager: lockfile first, then pyproject hints, then requirements.txt.
    if d.exists("uv.lock"):
        d.package_manager("uv", "uv.lock", "uv.lock", 1, "python.uv_lock")
    if d.exists("poetry.lock"):
        d.package_manager(
            "poetry", "poetry.lock", "poetry.lock", 1, "python.poetry_lock"
        )
    if d.exists("Pipfile.lock"):
        d.package_manager(
            "pipenv", "Pipfile.lock", "Pipfile.lock", 1, "python.pipfile_lock"
        )
    elif d.exists("Pipfile"):
        d.package_manager("pipenv", None, "Pipfile", 1, "python.pipfile")

    if pyproject is not None and not d.package_managers:
        text, data = pyproject
        tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
        if "poetry" in tool:
            line = find_key_line(text, "[tool.poetry")
            d.package_manager(
                "poetry", None, "pyproject.toml", line, "python.pyproject"
            )
        elif "uv" in tool:
            line = find_key_line(text, "[tool.uv")
            d.package_manager("uv", None, "pyproject.toml", line, "python.pyproject")
        else:
            d.package_manager("pip", None, "pyproject.toml", 1, "python.pyproject")
    if d.exists("requirements.txt") and not d.package_managers:
        d.package_manager("pip", None, "requirements.txt", 1, "python.requirements")


def _pyproject(d: Detection) -> tuple[str, dict] | None:
    text = d.read("pyproject.toml")
    if text is None:
        return None
    # Record the manifest even when it is malformed: it exists.
    d.manifest("pyproject.toml", "pyproject", "python.pyproject")
    data = d.toml("pyproject.toml", text)
    if data is None:
        return None

    project = data.get("project")
    if isinstance(project, dict) and isinstance(project.get("requires-python"), str):
        line = find_key_line(text, "requires-python")
        d.runtime(
            "python",
            project["requires-python"],
            "pyproject.toml",
            line,
            "python.requires_python",
        )
    else:
        tool = data.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
        if isinstance(deps, dict) and isinstance(deps.get("python"), str):
            line = find_key_line(text, "[tool.poetry.dependencies]", "python")
            d.runtime(
                "python", deps["python"], "pyproject.toml", line, "python.poetry_python"
            )
    return text, data
