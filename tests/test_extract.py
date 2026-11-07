"""T1.8: README extractors. One case per rule and a false-positive guard each."""

from __future__ import annotations

import pytest
from conftest import FIXTURES

from rtfmup.models import EnvVar, SourceRef
from rtfmup.readme.extract import _PLACEHOLDER, env_names, extract, merge_env
from rtfmup.readme.parse import parse
from rtfmup.readme.rst import parse_rst


def ex(md: str):
    return extract(parse(md), "README.md")


def cmds(md: str) -> list[str]:
    return [c.text for c in ex(md).commands]


def fence(lang: str, body: str) -> str:
    return f"# T\n\n```{lang}\n{body}\n```\n"


# --- commands: which fences ----------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "shell"),
    [
        ("bash", "bash"), ("sh", "sh"), ("shell", "sh"), ("zsh", "zsh"),
        ("console", "console"), ("shell-session", "console"),
        ("powershell", "powershell"), ("pwsh", "powershell"), ("ps1", "powershell"),
        ("cmd", "cmd"), ("bat", "cmd"),
    ],
)  # fmt: skip
def test_shell_fence_languages(lang, shell):
    prompt = "$ " if lang in ("console", "shell-session") else ""
    (c,) = ex(fence(lang, f"{prompt}make")).commands
    assert (c.text, c.shell, c.confidence) == ("make", shell, "medium")


@pytest.mark.parametrize(
    "lang", ["python", "json", "yaml", "javascript", "html", "sql"]
)
def test_non_shell_fences_yield_no_commands(lang):
    assert cmds(fence(lang, "npm install\n$ make")) == []


def test_unlabeled_fence_with_prompts_is_low_confidence():
    (a, b) = ex(fence("", "$ npm install\n$ npm start")).commands
    assert (a.text, b.text) == ("npm install", "npm start")
    assert a.confidence == "low" and a.shell == "console"


@pytest.mark.parametrize(
    "body",
    [
        "npm install\nnpm start",  # no prompts at all
        "$ npm install\nsome output\nmore output",  # prompts are a minority
        "const x = 1;\nconsole.log(x);",
    ],
)
def test_unlabeled_fence_without_majority_prompts_is_ignored(body):
    assert cmds(fence("", body)) == []


def test_unlabeled_block_opening_with_a_prompt_and_half_prompted_is_accepted():
    # `$ flask run` followed by one line of output: the common README pattern
    body = "$ flask run\n * Running on http://127.0.0.1:5000"
    (c,) = ex(fence("", body)).commands
    assert c.text == "flask run" and c.confidence == "low"


def test_half_prompted_block_not_opening_with_a_prompt_is_ignored():
    assert cmds(fence("", "output first\n$ flask run")) == []


def test_unlabeled_indented_block_under_install_heading_is_low_confidence():
    md = "# P\n\n## Installation\n\n    pip install datasette\n"
    (c,) = ex(md).commands
    assert c.text == "pip install datasette" and c.confidence == "low"
    assert c.evidence[0].detector == "readme.literal_block"


def test_unlabeled_indented_block_elsewhere_is_ignored():
    assert cmds("# P\n\n## License\n\n    pip install datasette\n") == []


def test_unlabeled_majority_prompt_drops_output_lines():
    assert cmds(fence("", "$ a\n$ b\nout")) == ["a", "b"]


@pytest.mark.parametrize(
    ("body", "shell"),
    [("PS> Get-Date\nPS> dir", "powershell"), ("> a\n> b", "unknown")],
)
def test_unlabeled_prompt_kinds(body, shell):
    assert ex(fence("", body)).commands[0].shell == shell


# --- commands: normalization ---------------------------------------------------


def test_prompt_and_comment_stripping_in_bash_fence():
    body = "# install deps\n$ npm ci\n   # indented comment\nnpm test\n#!/bin/sh"
    assert cmds(fence("bash", body)) == ["npm ci", "npm test"]


def test_gt_is_not_a_prompt_in_bash_fences():
    assert cmds(fence("bash", "> out.txt")) == ["> out.txt"]


def test_never_splits_on_and_and():
    assert cmds(fence("bash", "cd app && npm ci && npm start")) == [
        "cd app && npm ci && npm start"
    ]


def test_blank_lines_separate_but_do_not_merge():
    assert cmds(fence("bash", "a\n\nb")) == ["a", "b"]


def test_backslash_continuation_and_line_range():
    md = "# T\n\n```bash\npip install \\\n    -r requirements.txt \\\n    --upgrade\nmake\n```\n"
    a, b = ex(md).commands
    assert a.text == "pip install -r requirements.txt --upgrade"
    assert (a.evidence[0].line_start, a.evidence[0].line_end) == (4, 6)
    assert (b.evidence[0].line_start, b.evidence[0].line_end) == (7, 7)


def test_backslash_inside_command_is_not_a_continuation():
    assert cmds(fence("bash", "echo a\\ b")) == ["echo a\\ b"]


def test_powershell_backtick_continuation_and_prompts():
    body = "PS C:\\src> pip install `\n    -r requirements.txt\nPS> dir\n.\\run.ps1 # trailing"
    assert cmds(fence("powershell", body)) == [
        "pip install -r requirements.txt",
        "dir",
        ".\\run.ps1 # trailing",
    ]


def test_cmd_prompts_and_rem_comments():
    body = (
        "REM setup\nC:\\app> npm ci\n:: also a comment\nset PORT=3000 ^\n&& npm start"
    )
    assert cmds(fence("cmd", body)) == ["npm ci", "set PORT=3000 && npm start"]


def test_console_keeps_prompt_lines_and_drops_output():
    body = "$ pip install -r requirements.txt\nCollecting flask\n  Downloading x\n$ python app.py\n * Running on http://127.0.0.1:5000"
    assert cmds(fence("console", body)) == [
        "pip install -r requirements.txt",
        "python app.py",
    ]


def test_console_ps2_continuation():
    body = "$ docker run \\\n>   -p 80:80 \\\n>   nginx\nstarted"
    assert cmds(fence("console", body)) == ["docker run -p 80:80 nginx"]


def test_console_fixture():
    text = (FIXTURES / "console-output" / "README.md").read_text(encoding="utf-8")
    out = extract(parse(text), "README.md")
    assert [c.text for c in out.commands] == [
        "pip install -r requirements.txt",
        "python app.py",
    ]


def test_windows_fixture():
    text = (FIXTURES / "windows-readme" / "README.md").read_text(encoding="utf-8")
    out = extract(parse(text), "README.md")
    assert [(c.text, c.shell) for c in out.commands] == [
        ("py -m venv .venv", "powershell"),
        (".\\.venv\\Scripts\\Activate.ps1", "powershell"),
        ("pip install -r requirements.txt", "powershell"),
        ("py -m venv .venv", "powershell"),
        (".\\.venv\\Scripts\\Activate.ps1", "powershell"),
        ("python app.py", "powershell"),
    ]


def test_unix_only_fixture_commands():
    text = (FIXTURES / "unix-only" / "README.md").read_text(encoding="utf-8")
    texts = [c.text for c in extract(parse(text), "README.md").commands]
    assert texts[:2] == [
        "sudo apt install python3-venv libpq-dev",
        "python3 -m venv venv",
    ]
    assert "curl -fsSL https://example.com/install.sh | bash" in texts


def test_command_metadata():
    md = "# Top\n\n## Install\n\n```bash\nmake\n```\n"
    (c,) = ex(md).commands
    assert c.section_path == ["Top", "Install"]
    assert (c.category, c.reason, c.platform, c.variant) == (
        "other",
        "unclassified",
        "any",
        None,
    )
    assert (
        c.evidence[0].file == "README.md" and c.evidence[0].detector == "readme.fence"
    )
    assert c.evidence[0].line_start == 6


def test_commands_in_list_items_blockquotes_and_indented_code():
    md = "1. Install:\n\n   ```bash\n   npm ci\n   ```\n\n> ```sh\n> make\n> ```\n\n$ a\n$ b\n"
    # the last two lines form a paragraph, not code: only the fences count
    assert cmds(md) == ["npm ci", "make"]
    assert cmds("para\n\n    $ npm install\n    $ npm start\n") == [
        "npm install",
        "npm start",
    ]


# --- placeholders ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "export TOKEN=<your-token>",
        "curl -H YOUR_API_KEY x",
        "mkdir {name}",
        "run <your api key>",
    ],
)
def test_placeholders_flagged(text):
    (c,) = ex(fence("bash", text)).commands
    assert "placeholder" in c.flags


@pytest.mark.parametrize(
    "text",
    [
        "echo ${HOME}/x",
        "echo {a,b}",
        "cat < in > out",
        "make 2>&1",
        "echo $((1 + 2))",
        "npm ci",
    ],
)
def test_placeholder_false_positives(text):
    assert _PLACEHOLDER.search(text) is None
    (c,) = ex(fence("bash", text)).commands
    assert c.flags == []


# --- rST ---------------------------------------------------------------------


def test_rst_bare_literal_only_under_install_headings():
    install = "Install\n=======\n\nRun::\n\n   make\n"
    other = "License\n=======\n\nSee::\n\n   make\n"
    (c,) = extract(parse_rst(install), "README.rst").commands
    assert (c.text, c.confidence, c.evidence[0].detector) == (
        "make",
        "low",
        "readme.literal_block",
    )
    assert extract(parse_rst(other), "README.rst").commands == []


def test_rst_code_block_is_a_normal_fence():
    (c,) = extract(
        parse_rst("T\n=\n\n.. code-block:: bash\n\n   uv sync\n"), "README.rst"
    ).commands
    assert (c.text, c.confidence, c.evidence[0].line_start) == ("uv sync", "medium", 6)


# --- versions ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        ("Requires Node.js 20+.", [("node", "20+")]),
        (
            "## Prerequisites\n\nPython 3.11 and Go 1.22.",
            [("python", "3.11"), ("go", "1.22")],
        ),
        (
            "- Node 18.x\n- .NET 8\n- Ruby v3.3.0",
            [("node", "18.x"), ("dotnet", "8"), ("ruby", "3.3.0")],
        ),
        ("- golang 1.21+", [("go", "1.21+")]),
        ("This needs Java 17 and PHP 8.2.", [("java", "17"), ("php", "8.2")]),
    ],
)
def test_versions_found(md, expected):
    assert [(r.name, r.version_spec) for r in ex(md).runtimes] == expected


@pytest.mark.parametrize(
    "md",
    [
        "We love Python 3 code.",  # prose outside a requirements context
        "Go to step 2 and Rust is fun.",
        "Use `python3.11 -m venv` here.",  # code span, not prose
        "```bash\npython 3.11\n```",  # fences are not scanned for versions
        "## Usage\n\nIt is node 20 compatible.",
    ],
)
def test_version_false_positives(md):
    assert ex(md).runtimes == []


def test_version_evidence_and_confidence():
    (r,) = ex("# T\n\nRequires Node.js 20+.\n").runtimes
    assert r.confidence == "medium" and r.evidence[0].line_start == 3


# --- env vars ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        ("Set `DATABASE_URL` first.", {"DATABASE_URL"}),
        (fence("bash", "export API_KEY=abc\nPORT=3000 npm start"), {"API_KEY", "PORT"}),
        (fence("", "SECRET_KEY=changeme"), {"SECRET_KEY"}),
        (fence("bash", "echo $HOME_DIR"), {"HOME_DIR"}),
        (fence("powershell", "$env:AWS_REGION = 'x'"), {"AWS_REGION"}),
    ],
)
def test_env_vars_found(md, expected):
    assert {e.name for e in ex(md).env} == expected


@pytest.mark.parametrize(
    "md",
    [
        "Set DATABASE_URL in prose.",  # prose only, not code
        "Read `README`, `UTF_8`, `HTTP_404`, `HTTP_NOT_FOUND`.",
        "```python\nMAX_RETRIES = 3\n```",  # code in another language
        "Run `make` and `Npm_Thing`.",
        "`lowercase_name`",
        "```bash\nif [ $A == $B ]; then x; fi\n```",
    ],
)
def test_env_false_positives(md):
    assert ex(md).env == []


def test_env_http_proxy_is_kept():
    assert {e.name for e in ex("Use `HTTP_PROXY`.").env} == {"HTTP_PROXY"}


def test_env_defaults_are_never_taken_from_readme():
    (e,) = ex(fence("bash", "export API_KEY=sk-live-123")).env
    assert (e.default, e.required, e.secret_like) == (None, "unknown", True)


def test_env_names_function_assign_toggle():
    assert env_names("FOO=1 BAR_BAZ=2", allow_assign=True) == ["BAR_BAZ", "FOO"]
    assert env_names("FOO=1 BAR_BAZ=2", allow_assign=False) == ["BAR_BAZ"]


def test_env_table():
    md = (
        "| Variable | Required | Default | Description |\n|---|---|---|---|\n"
        "| `API_TOKEN` | yes | | Your API token |\n"
        "| `LOG_LEVEL` | no | `info` | Verbosity |\n"
        "| `PORT` | no | 8080 | Listen port |\n"
        "| not a var | yes | | nope |\n"
    )
    env = {e.name: e for e in ex(md).env}
    assert set(env) == {"API_TOKEN", "LOG_LEVEL", "PORT"}
    assert (env["API_TOKEN"].required, env["API_TOKEN"].description) == (
        "required",
        "Your API token",
    )
    assert (env["LOG_LEVEL"].required, env["LOG_LEVEL"].default) == ("optional", "info")
    assert env["PORT"].default == "8080" and env["API_TOKEN"].secret_like


def test_env_table_without_required_column_is_unknown():
    md = "| Name | Meaning |\n|---|---|\n| `DB_HOST` | host name |\n"
    (e,) = ex(md).env
    assert (e.required, e.description) == ("unknown", "host name")


def test_env_evidence_lines_in_fence():
    md = "# T\n\n```bash\nfoo\nexport DB_HOST=x\n```\n"
    (e,) = ex(md).env
    assert e.evidence[0].line_start == 5


def _var(name, required="required", desc=None, line=1, file=".env.example"):
    return EnvVar(
        name=name, required=required, secret_like=False, description=desc,
        evidence=[SourceRef(file=file, line_start=line, line_end=line, detector="x")],
    )  # fmt: skip


def test_merge_env_template_wins():
    template = [_var("A", "required"), _var("B", "optional", line=2)]
    readme = [
        _var("A", "unknown", desc="from readme", file="README.md"),
        _var("C", "unknown", file="README.md"),
    ]
    merged = merge_env(template, readme)
    by = {e.name: e for e in merged}
    assert [e.name for e in merged] == ["A", "B", "C"]
    assert by["A"].required == "required" and by["A"].description == "from readme"
    assert {ev.file for ev in by["A"].evidence} == {".env.example", "README.md"}
    assert by["C"].required == "unknown"
    assert template[0].description is None  # inputs are not mutated


# --- external accounts -----------------------------------------------------------


def test_account_from_keyword_paragraph():
    md = "# T\n\nSign up at [Acme Console](https://acme.example/console) and copy `ACME_API_KEY`.\n"
    (a,) = ex(md).external_accounts
    assert (a.name, a.url, a.related_env) == (
        "Acme Console",
        "https://acme.example/console",
        ["ACME_API_KEY"],
    )
    assert a.evidence[0].line_start == 3


def test_account_from_keyword_in_link_text():
    md = "See the [API key dashboard](https://x.example/keys).\n"
    assert [a.url for a in ex(md).external_accounts] == ["https://x.example/keys"]


@pytest.mark.parametrize(
    "md",
    [
        "See the [docs](https://x.example/docs) for details.",  # no keyword
        "Create an account with [mail](mailto:a@b.c).",
        "Create an account in [settings](/settings).",
        "Register at [site](ftp://x.example/).",
    ],
)
def test_account_false_positives(md):
    assert ex(md).external_accounts == []


def test_account_urls_are_deduplicated():
    md = "Sign up [here](https://a.example/).\n\nSign up [again](https://a.example/).\n"
    assert len(ex(md).external_accounts) == 1


def test_account_without_link_text_uses_domain():
    md = (
        "Get a token: [![badge](https://img.example/b.svg)](https://a.example/tokens)\n"
    )
    (a,) = ex(md).external_accounts
    assert a.url == "https://a.example/tokens"


def test_fullstack_fixture_account_and_ports():
    text = (FIXTURES / "fullstack-compose" / "README.md").read_text(encoding="utf-8")
    out = extract(parse(text), "README.md")
    assert [a.url for a in out.external_accounts] == [
        "https://dashboard.stripe.com/apikeys"
    ]
    assert [(p.port, p.url) for p in out.ports] == [(3000, "http://localhost:3000")]
    assert [c.text for c in out.commands] == [
        "cp .env.example .env",
        "docker compose up",
    ]


# --- ports -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        ("Open http://localhost:3000.", [(3000, "http://localhost:3000")]),
        ("Listens on localhost:8080 only.", [(8080, "http://localhost:8080")]),
        (
            "```bash\ncurl http://127.0.0.1:5000/health\n```",
            [(5000, "http://127.0.0.1:5000")],
        ),
        ("Bind to 0.0.0.0:8000.", [(8000, "http://0.0.0.0:8000")]),
        ("https://localhost:8443/x", [(8443, "https://localhost:8443")]),
    ],
)
def test_ports_found(md, expected):
    assert [(p.port, p.url) for p in ex(md).ports] == expected


@pytest.mark.parametrize(
    "md",
    [
        "Visit example.com:3000.",
        "localhost:99999",
        "localhost:1",
        "see localhost: soon",
        "mylocalhost:3000x",
    ],
)
def test_port_false_positives(md):
    assert ex(md).ports == []


def test_port_evidence_line_in_fence():
    md = "# T\n\n```bash\nfoo\ncurl localhost:9000\n```\n"
    (p,) = ex(md).ports
    assert p.evidence[0].line_start == 4 and p.evidence[0].line_end == 6


# --- whole fixtures ------------------------------------------------------------


@pytest.mark.parametrize(
    "name", sorted(p.name for p in FIXTURES.iterdir() if (p / "README.md").exists())
)
def test_every_fixture_extracts_without_error(name):
    text = (FIXTURES / name / "README.md").read_text(encoding="utf-8")
    n = len(text.splitlines())
    out = extract(parse(text), "README.md")
    for item in [
        *out.commands,
        *out.runtimes,
        *out.env,
        *out.external_accounts,
        *out.ports,
    ]:
        for ev in item.evidence:
            assert 1 <= ev.line_start <= ev.line_end <= n


def test_node_pnpm_fixture():
    text = (FIXTURES / "node-pnpm" / "README.md").read_text(encoding="utf-8")
    out = extract(parse(text), "README.md")
    assert [(r.name, r.version_spec) for r in out.runtimes] == [("node", "20+")]
    assert [c.text for c in out.commands] == [
        "pnpm install",
        "cp .env.example .env",
        "pnpm dev",
    ]
    assert [p.port for p in out.ports] == [3000]
