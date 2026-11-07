"""D17 allowlist: every translated form, and the things that must NOT translate."""

from __future__ import annotations

import pytest

from rtfmup.plan.translate import Translated, split_argv, to_windows


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("npm ci", ["npm", "ci"]),
        ("uv run python main.py", ["uv", "run", "python", "main.py"]),
        ('echo "a b"', ["echo", "a b"]),
        ("a | b", None),
        ("a && b", None),
        ("a || b", None),
        ("a; b", None),
        ("a > out", None),
        ("a < in", None),
        ("echo $(date)", None),
        ("echo `date`", None),
        ("unterminated 'quote", None),
        ("", None),
    ],
)
def test_split_argv(text, expected):
    assert split_argv(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "cp .env.example .env",
            Translated(
                shell_text="Copy-Item -LiteralPath '.env.example' -Destination '.env'"
            ),
        ),
        (
            "cp 'a b' c",
            Translated(shell_text="Copy-Item -LiteralPath 'a b' -Destination 'c'"),
        ),
        (
            "mkdir -p data logs",
            Translated(
                shell_text="New-Item -ItemType Directory -Force -Path 'data' | Out-Null; "
                "New-Item -ItemType Directory -Force -Path 'logs' | Out-Null"
            ),
        ),
        ("export PORT=8080", Translated(env={"PORT": "8080"})),
        ("export A=1 B=two", Translated(env={"A": "1", "B": "two"})),
    ],
)
def test_translated(text, expected):
    assert to_windows(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "cp -r a b",  # flags are not on the allowlist
        "cp a b c",
        "cp a",
        "mkdir data",  # without -p
        "mkdir -p -m 700 data",
        "chmod +x run.sh",
        "source venv/bin/activate",
        "sudo apt install x",
        "ln -s a b",
        "export",
        "export A",
        "cp a b && echo hi",
        "export A=$(whoami)",
        'export FZF_ALT_C_OPTS="',  # unterminated quote (multi-line value in a README)
        "export A='x",
    ],
)
def test_not_translated(text):
    assert to_windows(text) is None
