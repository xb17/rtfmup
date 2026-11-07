"""T1.5: containers, env templates, task runners, devcontainer, CI, deployment."""

from __future__ import annotations

import pytest
from conftest import FIXTURES

from rtfmup.detect import detect_dir
from rtfmup.detect.containers import image_service, published_port
from rtfmup.detect.envfile import parse_template, parse_value


def run(name: str):
    return detect_dir(FIXTURES / name)


# --- env templates -----------------------------------------------------------

ENV_CASES = [
    # (line, name, value, comment, commented_out)
    ("KEY=value", "KEY", "value", None, False),
    ("KEY=", "KEY", "", None, False),
    ("KEY=   # from the dashboard", "KEY", "", "from the dashboard", False),
    ("export KEY=value", "KEY", "value", None, False),
    ('KEY="quoted value"', "KEY", "quoted value", None, False),
    ("KEY='single'", "KEY", "single", None, False),
    ('KEY="has # hash"  # real comment', "KEY", "has # hash", "real comment", False),
    ("KEY=a#b", "KEY", "a#b", None, False),  # no whitespace before '#': part of value
    ("KEY=val  # note", "KEY", "val", "note", False),
    ('KEY=""', "KEY", "", None, False),
    ("# KEY=value", "KEY", "value", None, True),
    ("# KEY=", "KEY", "", None, True),
    ("#KEY=value", "KEY", "value", None, True),
    ("# export KEY=value", "KEY", "value", None, True),
    ("  KEY = spaced", "KEY", "spaced", None, False),
]


@pytest.mark.parametrize(("line", "name", "value", "comment", "commented"), ENV_CASES)
def test_env_line_table(line, name, value, comment, commented):
    assert parse_template(line) == [(name, value, comment, commented, 1)]


@pytest.mark.parametrize(
    "line",
    [
        "# Just a comment",
        "# Note: set A=B for tests",
        "",
        "not a variable",
        "# Required",
    ],
)
def test_env_non_variable_lines_ignored(line):
    assert parse_template(line) == []


def test_env_crlf():
    assert parse_template("A=1\r\nB=\r\n") == [
        ("A", "1", None, False, 1),
        ("B", "", None, False, 2),
    ]


def test_env_unterminated_quote():
    assert parse_value('"abc') == ("abc", None)


def test_env_bom(tmp_path):
    (tmp_path / ".env.example").write_bytes(b"\xef\xbb\xbfFIRST=\nSECOND=2\n")
    d = detect_dir(tmp_path)
    assert [(e.name, e.required) for e in d.env] == [
        ("FIRST", "required"),
        ("SECOND", "optional"),
    ]


def test_env_fullstack_required_and_secret():
    env = {e.name: e for e in run("fullstack-compose").env}
    assert env["SECRET_KEY"].required == "required" and env["SECRET_KEY"].secret_like
    assert env["STRIPE_API_KEY"].secret_like
    assert (
        env["DATABASE_URL"].required == "optional"
        and not env["DATABASE_URL"].secret_like
    )
    assert env["REDIS_URL"].required == "optional"
    assert env["SENTRY_DSN"].required == "optional"  # commented out
    assert env["SENTRY_DSN"].evidence[0].line_start == 7


def test_env_node_pnpm_description_and_line():
    env = {e.name: e for e in run("node-pnpm").env}
    assert env["API_KEY"].description == "from the provider dashboard"
    assert env["API_KEY"].required == "required"
    assert env["PORT"].default == "3000"
    assert env["DEBUG"].required == "optional"
    assert env["PORT"].evidence[0].line_start == 2


def test_real_env_file_is_never_read(tmp_path):
    (tmp_path / ".env").write_text("SECRET=hunter2\n")
    d = detect_dir(tmp_path)
    assert d.env == [] and d.manifests == []


# --- containers --------------------------------------------------------------


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("postgres:16", "postgres"),
        ("docker.io/library/redis:7-alpine", "redis"),
        ("ghcr.io/acme/mongo@sha256:abc", "mongodb"),
        ("pgvector/pgvector:pg16", "postgres"),
        ("myorg/postgres-exporter:1", None),
        ("node:20", None),
    ],
)
def test_image_service_table(image, expected):
    assert image_service(image) == expected


@pytest.mark.parametrize(
    ("entry", "port"),
    [
        ("8000:8000", 8000),
        ("127.0.0.1:5432:5432", 5432),
        ("5432:5432/tcp", 5432),
        ("8000-8010:8000-8010", 8000),
        ("${PORT:-8080}:80", 8080),
        ("3000", None),  # container port only: ephemeral host port, not guessed
        (3000, None),
        ({"target": 5432, "published": 5433}, 5433),
        ({"target": 5432, "published": "5433"}, 5433),
        ({"target": 5432}, None),
        ("${PORT}:80", None),
    ],
)
def test_compose_port_syntaxes(entry, port):
    assert published_port(entry)[0] == port


def test_compose_fullstack():
    d = run("fullstack-compose")
    assert d.has_docker
    assert {(s.name, tuple(s.ports)) for s in d.services} == {
        ("postgres", (5432,)),
        ("redis", (6379,)),
    }
    assert [(p.port, p.url) for p in d.ports] == [(3000, "http://localhost:3000")]
    assert {m.kind for m in d.manifests} >= {"dockerfile", "compose", "env_template"}


def test_compose_docker_or_native_lines():
    d = run("docker-or-native")
    (svc,) = d.services
    assert svc.name == "postgres" and svc.ports == [5432]
    assert svc.evidence[0].line_start == 2
    assert [p.port for p in d.ports] == [8000]


def test_compose_invalid_yaml_warns(tmp_path):
    (tmp_path / "compose.yaml").write_text("services: [unclosed\n")
    d = detect_dir(tmp_path)
    assert (
        d.has_docker
        and len(d.warnings) == 1
        and d.warnings[0].startswith("compose.yaml:")
    )


def test_no_docker_in_plain_fixture():
    assert not run("py-uv").has_docker


# --- task runners ------------------------------------------------------------


def test_makefile(tmp_path):
    (tmp_path / "Makefile").write_text(
        "CC := gcc\n.PHONY: build test\n\nbuild:\n\tgo build ./...\n\ntest lint: build\n\tgo test ./...\n"
        "pattern-%.o: x\n\tfoo\nVAR = a:b\n"
    )
    d = detect_dir(tmp_path)
    assert d.scripts == {
        "make:build": "go build ./...",
        "make:test": "go test ./...",
        "make:lint": "go test ./...",
    }
    assert [m.kind for m in d.manifests] == ["makefile"]


def test_justfile(tmp_path):
    (tmp_path / "justfile").write_text(
        'set shell := ["bash", "-c"]\nname := "x"\n\ndev port="8000":\n    uv run app {{port}}\n\n@test:\n    pytest\n'
    )
    assert detect_dir(tmp_path).scripts == {
        "just:dev": "uv run app {{port}}",
        "just:test": "pytest",
    }


def test_taskfile(tmp_path):
    (tmp_path / "Taskfile.yml").write_text(
        "version: '3'\ntasks:\n  build:\n    cmds:\n      - go build\n      - echo done\n  hello: echo hi\n"
    )
    assert detect_dir(tmp_path).scripts == {
        "task:build": "go build\necho done",
        "task:hello": "echo hi",
    }


def test_package_json_scripts_unprefixed():
    assert run("node-pnpm").scripts == {
        "dev": "node server.js",
        "start": "node server.js",
    }


def test_taskfile_invalid_yaml_warns(tmp_path):
    (tmp_path / "Taskfile.yml").write_text("tasks: [\n")
    assert len(detect_dir(tmp_path).warnings) == 1


# --- devcontainer ------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ".devcontainer/devcontainer.json",
        ".devcontainer.json",
        ".devcontainer/py/devcontainer.json",
    ],
)
def test_devcontainer_locations(tmp_path, path):
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{ // jsonc comment\n "name": "x" }\n')
    d = detect_dir(tmp_path)
    assert d.has_devcontainer and d.warnings == []


def test_no_devcontainer():
    assert not run("py-uv").has_devcontainer


# --- CI ----------------------------------------------------------------------

WORKFLOW = """\
name: ci
on: push
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - run: uv sync
      - name: multi
        shell: pwsh
        run: |
          # comment
          uv run ruff check .
          uv run pytest `
            -x
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: make lint
"""


def test_ci_run_steps(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text(WORKFLOW)
    d = detect_dir(tmp_path)
    got = [
        (c.text, c.shell, c.platform, c.section_path, c.evidence[0].line_start)
        for c in d.commands
    ]
    assert got == [
        ("uv sync", "powershell", "windows", ["ci", "test"], 8),  # default: pwsh
        ("uv run ruff check .", "powershell", "windows", ["ci", "test"], 13),
        ("uv run pytest -x", "powershell", "windows", ["ci", "test"], 14),
        ("make lint", "bash", "linux", ["ci", "lint"], 19),  # default: bash
    ]
    assert all(
        c.confidence == "low" and c.evidence[0].detector == "ci.github"
        for c in d.commands
    )
    assert [c.id for c in d.commands] == ["cmd-1", "cmd-2", "cmd-3", "cmd-4"]


def test_ci_backslash_continuation(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "b.yaml").write_text(
        "jobs:\n  a:\n    runs-on: macos-latest\n    steps:\n      - run: |\n          pip install \\\n            -r req.txt\n"
    )
    (cmd,) = detect_dir(tmp_path).commands
    assert cmd.text == "pip install -r req.txt" and cmd.platform == "macos"


def test_ci_default_shell_follows_the_runner_os(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "s.yml").write_text(
        "jobs:\n"
        "  mac:\n    runs-on: macos-latest\n    steps:\n      - run: make a\n"
        "  win-bash:\n    runs-on: windows-latest\n    steps:\n"
        "      - shell: bash\n        run: make b\n"
        "  win-default:\n    runs-on: windows-latest\n"
        "    defaults:\n      run:\n        shell: cmd\n"
        "    steps:\n      - run: make c\n"
        "  matrix:\n    runs-on: ${{ matrix.os }}\n    steps:\n      - run: make d\n"
    )
    got = {c.text: (c.shell, c.platform) for c in detect_dir(tmp_path).commands}
    assert got == {
        "make a": ("bash", "macos"),
        "make b": ("bash", "windows"),  # an explicit shell always wins
        "make c": ("cmd", "windows"),  # so does defaults.run.shell
        "make d": ("unknown", "any"),  # cannot tell from a matrix expression
    }


def test_ci_invalid_yaml_warns(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "bad.yml").write_text("jobs: [\n")
    d = detect_dir(tmp_path)
    assert d.commands == [] and len(d.warnings) == 1


def test_ci_none_in_plain_fixture():
    assert run("py-uv").commands == []


# --- deployment --------------------------------------------------------------


def test_deploy_mixed():
    d = run("deploy-mixed")
    got = {(x.kind, x.file, x.summary) for x in d.deployments}
    assert got == {
        ("procfile", "Procfile", "web: gunicorn app:app; worker: python worker.py"),
        ("fly", "fly.toml", "app: deploy-mixed"),
        ("vercel", "vercel.json", None),
    }
    (cmd,) = d.commands
    assert (cmd.text, cmd.category, cmd.confidence, cmd.evidence[0].line_start) == (
        "gunicorn app:app",
        "run",
        "low",
        1,
    )
    assert cmd.evidence[0].detector == "deploy.procfile"


def test_k8s_manifests_and_decoy():
    d = run("k8s-manifests")
    got = sorted((x.kind, x.file, x.summary) for x in d.deployments)
    assert got == [
        ("helm", "charts/web/Chart.yaml", "helm chart web"),
        ("k8s", "k8s/deployment.yaml", "k8s Deployment/web"),
        ("k8s", "k8s/service.yaml", "k8s Service/web"),
    ]
    # k8s/decoy.yaml has `kind:` but no `apiVersion:` and is ignored.
    assert not any("decoy" in x.file for x in d.deployments)


def test_k8s_multi_document_file(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "all.yaml").write_text(
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: a\n---\napiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: b\n"
    )
    d = detect_dir(tmp_path)
    assert [x.summary for x in d.deployments] == ["k8s Service/a", "k8s Deployment/b"]
    assert [x.evidence[0].line_start for x in d.deployments] == [2, 7]


def test_k8s_ignores_ci_yaml_and_helm_templates(tmp_path):
    (tmp_path / "ci.yml").write_text(
        "kind: x\napiVersion: 3\n"
    )  # apiVersion not a string
    t = tmp_path / "charts" / "x" / "templates"
    t.mkdir(parents=True)
    (t / "d.yaml").write_text("apiVersion: apps/v1\nkind: {{ .Values.kind }}\n")
    assert detect_dir(tmp_path).deployments == []


def test_kustomize_not_counted_as_k8s(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\nkind: Kustomization\nresources: []\n"
    )
    assert [x.kind for x in detect_dir(tmp_path).deployments] == ["kustomize"]


def test_k8s_file_cap_warns(tmp_path):
    (tmp_path / "k8s").mkdir()
    for i in range(205):
        (tmp_path / "k8s" / f"f{i:03}.yaml").write_text("a: 1\n")
    d = detect_dir(tmp_path)
    assert any("only the first 200" in w for w in d.warnings)


def test_k8s_large_file_skipped_with_warning(tmp_path):
    (tmp_path / "k8s").mkdir()
    (tmp_path / "k8s" / "big.yaml").write_text("# " + "x" * 1_000_001 + "\n")
    d = detect_dir(tmp_path)
    assert any("larger than" in w for w in d.warnings) and d.deployments == []


@pytest.mark.parametrize(
    ("name", "content", "kind", "summary"),
    [
        ("netlify.toml", '[build]\ncommand = "x"\n', "netlify", None),
        ("render.yaml", "services: []\n", "render", None),
        ("railway.json", "{}", "railway", None),
        ("wrangler.toml", 'name = "worker"\n', "cloudflare_wrangler", "name: worker"),
        ("wrangler.jsonc", '{ // c\n "name": "w" }', "cloudflare_wrangler", None),
        ("serverless.yml", "service: api\n", "serverless", "service: api"),
    ],
)
def test_simple_deploy_files(tmp_path, name, content, kind, summary):
    (tmp_path / name).write_text(content)
    d = detect_dir(tmp_path)
    assert [(x.kind, x.file, x.summary) for x in d.deployments] == [
        (kind, name, summary)
    ]


def test_app_json_heroku_vs_expo(tmp_path):
    (tmp_path / "app.json").write_text('{"expo": {"name": "x"}}')
    assert detect_dir(tmp_path).deployments == []
    (tmp_path / "app.json").write_text('{"name": "x", "buildpacks": []}')
    assert [x.kind for x in detect_dir(tmp_path).deployments] == ["heroku_app_json"]


def test_procfile_without_web(tmp_path):
    (tmp_path / "Procfile").write_text("worker: python w.py\n# note\n")
    d = detect_dir(tmp_path)
    assert d.commands == [] and len(d.deployments) == 1


def test_procfile_empty(tmp_path):
    (tmp_path / "Procfile").write_text("\n")
    d = detect_dir(tmp_path)
    assert d.deployments == [] and d.commands == []
