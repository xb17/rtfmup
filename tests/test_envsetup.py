"""T3.4 env setup tests. Temp dirs and simulated input only."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from rtfmup.execute.envsetup import (
    Prompter,
    defined_keys,
    format_value,
    setup_env,
)
from rtfmup.models import EnvVar

TEMPLATE = (
    "# app settings\n"
    "PORT=3000\n"
    "DATABASE_URL=            # postgres url\n"
    "API_TOKEN=\n"
    "# DEBUG=1\n"
    "export SESSION_SECRET=\n"
    "\n"
    "LOG_LEVEL=info\n"
)


def var(name, required="required", secret=False, default=None, desc=None):
    return EnvVar(
        name=name,
        required=required,
        secret_like=secret,
        default=default,
        description=desc,
        evidence=[
            {"file": ".env.example", "line_start": 1, "detector": "env.template"}
        ],
    )


VARS = [
    var("PORT", "optional", default="3000"),
    var("DATABASE_URL", desc="postgres url"),
    var("API_TOKEN", secret=True),
    var("DEBUG", "optional", default="1"),
    var("SESSION_SECRET", secret=True),
    var("LOG_LEVEL", "optional", default="info"),
]


class Terminal:
    """Scripted answers; records which prompts used the hidden-input path."""

    def __init__(self, plain=(), secret=()):
        self.plain, self.secret = list(plain), list(secret)
        self.plain_prompts: list[str] = []
        self.secret_prompts: list[str] = []
        self.out = io.StringIO()

    def read(self, prompt):
        self.plain_prompts.append(prompt)
        if not self.plain:
            raise EOFError
        return self.plain.pop(0)

    def getpass(self, prompt):
        self.secret_prompts.append(prompt)
        if not self.secret:
            raise EOFError
        return self.secret.pop(0)

    def prompter(self, interactive=True):
        return Prompter(
            interactive=interactive, read=self.read, secret=self.getpass, out=self.out
        )


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".env.example").write_text(TEMPLATE, encoding="utf-8", newline="")
    return tmp_path


def env_text(repo: Path) -> str:
    return (repo / ".env").read_text(encoding="utf-8")


# --- creating -------------------------------------------------------------


def test_creates_env_and_fills_required(repo):
    t = Terminal(plain=["postgres://db/app"], secret=["tok-123", "sess-456"])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert r.created and r.complete
    assert r.filled == ["DATABASE_URL", "API_TOKEN", "SESSION_SECRET"]
    text = env_text(repo)
    assert "DATABASE_URL=postgres://db/app  # postgres url\n" in text
    assert "API_TOKEN=tok-123\n" in text
    assert "export SESSION_SECRET=sess-456\n" in text
    # untouched: optional values, comments, the commented-out DEBUG, blank line
    assert text.startswith("# app settings\nPORT=3000\n")
    assert "# DEBUG=1\n" in text and "LOG_LEVEL=info\n" in text


def test_secret_like_variables_use_hidden_input(repo):
    t = Terminal(plain=["x"], secret=["a", "b"])
    setup_env(repo, ".env.example", VARS, t.prompter())
    assert [p.split()[0] for p in t.plain_prompts] == ["DATABASE_URL"]
    assert [p.split()[0] for p in t.secret_prompts] == ["API_TOKEN", "SESSION_SECRET"]


def test_only_required_variables_are_prompted(repo):
    t = Terminal(plain=["x"], secret=["a", "b"])
    setup_env(repo, ".env.example", VARS, t.prompter())
    asked = [p.split()[0] for p in t.plain_prompts + t.secret_prompts]
    assert "PORT" not in asked and "LOG_LEVEL" not in asked and "DEBUG" not in asked


def test_blank_answer_leaves_variable_empty_and_reports_it(repo):
    t = Terminal(plain=[""], secret=["tok", ""])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert r.created and not r.complete
    assert r.unfilled == ["DATABASE_URL", "SESSION_SECRET"]
    assert "DATABASE_URL=            # postgres url" in env_text(repo)
    assert "Still empty: DATABASE_URL, SESSION_SECRET" in t.out.getvalue()


def test_non_interactive_copies_template_and_prompts_nothing(repo):
    t = Terminal()
    r = setup_env(repo, ".env.example", VARS, t.prompter(interactive=False))
    assert r.created and r.filled == []
    assert r.unfilled == ["DATABASE_URL", "API_TOKEN", "SESSION_SECRET"]
    assert env_text(repo) == TEMPLATE
    assert not t.plain_prompts and not t.secret_prompts


def test_eof_mid_prompt_still_creates_a_usable_env(repo):
    t = Terminal(plain=["db"], secret=[])  # getpass hits EOF
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert r.filled == ["DATABASE_URL"]
    assert r.unfilled == ["API_TOKEN", "SESSION_SECRET"]


def test_crlf_template_stays_crlf(tmp_path):
    (tmp_path / ".env.example").write_bytes(b"A=\r\nB=2\r\n")
    t = Terminal(plain=["one"])
    setup_env(tmp_path, ".env.example", [var("A"), var("B", "optional")], t.prompter())
    assert (tmp_path / ".env").read_bytes() == b"A=one\r\nB=2\r\n"


def test_template_in_a_subdirectory_writes_env_beside_it(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / ".env.example").write_text("KEY_X=\n")
    t = Terminal(secret=["v"])
    r = setup_env(
        tmp_path,
        "web/.env.example",
        [var("KEY_X", secret=True)],
        t.prompter(),
        cwd="web",
    )
    assert r.created and (tmp_path / "web" / ".env").read_text() == "KEY_X=v\n"
    assert not (tmp_path / ".env").exists()


# --- values never leak ----------------------------------------------------


def test_values_never_appear_in_messages_or_result(repo):
    t = Terminal(plain=["hunter2-db"], secret=["tok-SECRET-1", "sess-SECRET-2"])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    blob = t.out.getvalue() + repr(r) + "".join(t.plain_prompts + t.secret_prompts)
    for value in ("hunter2-db", "tok-SECRET-1", "sess-SECRET-2"):
        assert value not in blob


# --- an existing .env -----------------------------------------------------


def test_existing_env_is_never_modified_when_declined(repo):
    original = "PORT=9999\nAPI_TOKEN=keepme\n"
    (repo / ".env").write_text(original)
    t = Terminal(plain=["n"])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert env_text(repo) == original
    assert r.skipped == "declined" and not r.created
    assert "DATABASE_URL" in r.unfilled


def test_existing_env_is_untouched_in_non_interactive_mode(repo):
    original = "PORT=9999\n"
    (repo / ".env").write_text(original)
    r = setup_env(repo, ".env.example", VARS, Terminal().prompter(interactive=False))
    assert env_text(repo) == original and r.skipped == "declined"


def test_append_adds_only_missing_keys_and_keeps_existing_lines(repo):
    original = "PORT=9999\nAPI_TOKEN=keepme\nSESSION_SECRET=s\nLOG_LEVEL=debug\n"
    (repo / ".env").write_text(original)
    t = Terminal(plain=["y", "postgres://x"])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    text = env_text(repo)
    assert text.startswith(original)  # existing content byte-for-byte
    assert text[len(original) :] == "DATABASE_URL=postgres://x\n"
    assert r.appended == ["DATABASE_URL"] and r.filled == ["DATABASE_URL"]
    assert "keepme" not in t.out.getvalue()


def test_commented_out_template_variables_are_not_appended(repo):
    (repo / ".env").write_text(
        "PORT=1\nDATABASE_URL=a\nAPI_TOKEN=b\nSESSION_SECRET=c\n"
    )
    r = setup_env(repo, ".env.example", VARS, Terminal(plain=["y"]).prompter())
    assert r.skipped is None or "DEBUG" not in r.appended
    assert "DEBUG" not in env_text(repo)
    assert r.appended == ["LOG_LEVEL"]
    assert env_text(repo).endswith("LOG_LEVEL=info\n")


def test_commented_line_in_env_does_not_count_as_defined(repo):
    (repo / ".env").write_text(
        "# DATABASE_URL=old\nPORT=1\nAPI_TOKEN=b\nSESSION_SECRET=c\nLOG_LEVEL=i\n"
    )
    t = Terminal(plain=["y", "new"])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert r.appended == ["DATABASE_URL"]
    assert env_text(repo).endswith("# DATABASE_URL=old\n") is False
    assert "DATABASE_URL=new\n" in env_text(repo)


def test_append_handles_missing_trailing_newline_and_crlf(repo):
    (repo / ".env").write_bytes(b"PORT=1\r\nLOG_LEVEL=i")
    (repo / ".env.example").write_text("PORT=1\nLOG_LEVEL=i\nNEW_FLAG=on\n")
    r = setup_env(
        repo,
        ".env.example",
        [var("NEW_FLAG", "optional", default="on")],
        Terminal(plain=["y"]).prompter(),
    )
    assert r.appended == ["NEW_FLAG"]
    assert (repo / ".env").read_bytes() == b"PORT=1\r\nLOG_LEVEL=i\r\nNEW_FLAG=on\r\n"


def test_nothing_missing_does_not_prompt_or_write(repo):
    (repo / ".env").write_text(TEMPLATE)
    t = Terminal()
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert r.skipped == "nothing missing"
    assert env_text(repo) == TEMPLATE and not t.plain_prompts


def test_defined_keys_ignores_comments_and_values():
    assert defined_keys("A=1\n# B=2\nexport C='x y'\n\n") == {"A", "C"}


# --- hostile input --------------------------------------------------------


def test_value_with_a_line_break_is_rejected_not_written(repo):
    t = Terminal(plain=["a\nEVIL=1"], secret=["", ""])
    r = setup_env(repo, ".env.example", VARS, t.prompter())
    assert "EVIL" not in env_text(repo)
    assert "DATABASE_URL" in r.unfilled


def test_template_outside_the_repo_is_refused(tmp_path):
    outside = tmp_path / "outside.env"
    outside.write_text("A=\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    r = setup_env(repo, "../outside.env", [var("A")], Terminal().prompter())
    assert r.skipped and "outside the repo" in r.skipped
    assert not (repo / ".env").exists()


def test_symlinked_template_pointing_outside_is_refused(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("A=\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        os.symlink(outside, repo / ".env.example")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    r = setup_env(repo, ".env.example", [var("A")], Terminal().prompter())
    assert r.skipped and not (repo / ".env").exists()


def test_env_symlink_is_not_written_through(repo, tmp_path_factory):
    victim = tmp_path_factory.mktemp("victim") / "target"
    victim.write_text("precious\n")
    try:
        os.symlink(victim, repo / ".env")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    r = setup_env(repo, ".env.example", VARS, Terminal(plain=["y"]).prompter())
    assert r.skipped and "symlink" in r.skipped
    assert victim.read_text() == "precious\n"


# --- quoting --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("plain", "plain"),
        ("with space", "'with space'"),
        ("a#b", "'a#b'"),
        ("cost$5", "'cost$5'"),
        ("it's", '"it\'s"'),
        ('say "hi" it\'s', '"say \\"hi\\" it\'s"'),
        ("back\\slash", "'back\\slash'"),
    ],
)
def test_format_value(value, expected):
    assert format_value(value) == expected


def test_written_values_round_trip_through_the_detector_parser(repo):
    from rtfmup.detect.envfile import parse_template

    value = "p@ss word#1"
    t = Terminal(plain=["db"], secret=[value, "s"])
    setup_env(repo, ".env.example", VARS, t.prompter())
    parsed = {
        n: v
        for n, v, _c, commented, _l in parse_template(env_text(repo))
        if not commented
    }
    assert parsed["API_TOKEN"] == value


def test_create_never_overwrites_a_file_that_appears_late(repo):
    """The exists() check and the write are separate; the write itself must refuse."""
    from rtfmup.execute.envsetup import _create

    (repo / ".env").write_text("mine\n")
    t = Terminal(plain=["x"], secret=["a", "b"])
    r = _create(repo / ".env", TEMPLATE, [var("DATABASE_URL")], t.prompter())
    assert r.skipped and not r.created
    assert env_text(repo) == "mine\n"
