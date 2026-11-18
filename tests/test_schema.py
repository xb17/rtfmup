import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from rtfmup.models import Analysis, Command, SourceRef

SCHEMA_FILE = Path(__file__).parents[1] / "schema" / "analysis.v1.json"


def make_analysis() -> Analysis:
    ref = SourceRef(file="README.md", line_start=3, line_end=5, detector="readme.fence")
    return Analysis(
        rtfmup_version="0.0.0",
        root="/tmp/x",
        git_head=None,
        git_dirty=None,
        readme="README.md",
        readme_format="markdown",
        components=[],
        runtimes=[],
        package_managers=[],
        manifests=[],
        commands=[
            Command(
                id="cmd-1",
                text="uv sync",
                shell="bash",
                category="install",
                reason="prefix:uv sync",
                section_path=["Installation"],
                confidence="medium",
                evidence=[ref],
            )
        ],
        env=[],
        services=[],
        external_accounts=[],
        ports=[],
        deployments=[],
        has_devcontainer=False,
        has_docker=False,
        conflicts=[],
        ambiguities=[],
        warnings=[],
    )


def test_committed_schema_matches_models():
    expected = json.dumps(Analysis.model_json_schema(), indent=2) + "\n"
    assert SCHEMA_FILE.read_bytes() == expected.encode("utf-8")


def test_schema_command_output_matches_committed_file():
    out = subprocess.run(
        [sys.executable, "-m", "rtfmup.cli", "schema"],
        capture_output=True,
        check=True,
    ).stdout
    assert out == SCHEMA_FILE.read_bytes()


def test_round_trip():
    a = make_analysis()
    assert Analysis.model_validate_json(a.model_dump_json()) == a


def test_extra_keys_forbidden():
    data = make_analysis().model_dump()
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        Analysis.model_validate(data)


def test_extra_keys_forbidden_in_nested_models():
    data = make_analysis().model_dump()
    data["commands"][0]["evidence"][0]["extra"] = True
    with pytest.raises(ValidationError):
        Analysis.model_validate(data)


def test_defaults_applied():
    c = make_analysis().commands[0]
    assert (c.platform, c.variant, c.needs_shell, c.flags) == ("any", None, False, [])


SCHEMA_MD = Path(__file__).parents[1] / "docs" / "schema.md"


def test_schema_markdown_matches_the_generator():
    from rtfmup.schema_md import render_markdown

    assert SCHEMA_MD.read_bytes().decode("utf-8") == render_markdown(), (
        "docs/schema.md is stale: run `uv run rtfmup schema --markdown > docs/schema.md`"
    )


def test_schema_markdown_cli_output_is_the_generator_output():
    from rtfmup.schema_md import render_markdown

    out = subprocess.run(
        [sys.executable, "-m", "rtfmup.cli", "schema", "--markdown"],
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    assert out == render_markdown()


def test_schema_markdown_covers_every_model():
    text = SCHEMA_MD.read_text(encoding="utf-8")
    for name in Analysis.model_json_schema()["$defs"]:
        assert f"## {name}\n" in text
