"""Benchmark harness (T1.12). No network, no real corpus: fixtures stand in for repos."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml
from conftest import FIXTURES

BENCH = Path(__file__).parents[1] / "bench"
sys.path.insert(0, str(BENCH))

from fetch import checkout_dir, load_corpus
from metrics import Gold, GoldStep, normalize, score_repo
from run import load_gold, main, render, run_bench

from rtfmup.analyze import build_analysis

CORPUS = load_corpus(BENCH / "corpus.toml")


@pytest.fixture(autouse=True)
def fixed_host(monkeypatch):
    """Benchmark runs probe the host; tests use a fixed one instead."""
    from hosts import windows_full

    monkeypatch.setattr("run.probe_host", windows_full)


# --- the committed corpus and gold files ---------------------------------------------


def test_corpus_has_the_ten_repos_with_pinned_shas():
    assert len(CORPUS) == 10
    for repo in CORPUS:
        assert re.fullmatch(r"[0-9a-f]{40}", repo["sha"]), repo["name"]
        assert repo["url"].startswith("https://github.com/")
        assert {"name", "ecosystem", "why", "e2e"} <= set(repo)


def test_held_out_set_is_datasette_ripgrep_laravel():
    assert {r["name"] for r in CORPUS if r.get("held_out")} == {
        "datasette",
        "ripgrep",
        "laravel",
    }


def test_e2e_flags_match_the_plan():
    not_e2e = {r["name"] for r in CORPUS if not r["e2e"]}
    assert not_e2e == {"full-stack-fastapi-template", "eshop", "self-hosted"}


@pytest.mark.parametrize("repo", CORPUS, ids=[r["name"] for r in CORPUS])
def test_each_gold_file_is_valid_and_matches_its_corpus_entry(repo):
    gold = load_gold(BENCH / "gold", repo["name"])
    assert gold.repo == repo["name"]
    # no duplicate steps
    keys = [(normalize(s.command), s.category) for s in gold.steps]
    assert len(keys) == len(set(keys))
    # test and lint commands are not setup steps
    assert all(s.category not in ("test", "lint") for s in gold.steps)
    assert all("\n" not in s.command for s in gold.steps)


def test_every_gold_file_is_in_the_corpus():
    names = {p.stem for p in (BENCH / "gold").glob("*.yaml")}
    assert names == {r["name"] for r in CORPUS}


def test_checkout_dir_is_in_the_cache_not_in_this_repo():
    d = checkout_dir(CORPUS[0], Path("/cache"))
    assert d.parent == Path("/cache") and d.name.startswith(CORPUS[0]["name"] + "-")


def test_gold_schema_rejects_unknown_keys_and_bad_categories():
    base = {
        "repo": "x",
        "variant": "native",
        "steps": [],
        "runtimes": [],
        "env_required": [],
        "services": [],
    }
    Gold.model_validate(base)
    with pytest.raises(ValueError):
        Gold.model_validate({**base, "surprise": 1})
    with pytest.raises(ValueError):
        GoldStep.model_validate({"category": "deploy", "command": "x"})


# --- normalization ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  npm   install  ", "npm install"),
        ("$ npm install", "npm install"),
        ("PS> Get-Date", "Get-Date"),
        ("PS C:\\x> dir", "dir"),
        ("> make", "make"),
        ("make;", "make"),
        ("make ;", "make"),
        ("NPM install", "NPM install"),  # case-sensitive
        ("echo $HOME", "echo $HOME"),  # `$` inside the command is untouched
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


# --- scoring on fixtures ----------------------------------------------------------------


def make_gold(steps, env=()):
    return Gold(
        repo="x", variant="native", steps=[GoldStep(**s) for s in steps],
        runtimes=[], env_required=list(env), services=[],
    )  # fmt: skip


def score(fixture, steps, env=()):
    a = build_analysis(FIXTURES / fixture)
    return score_repo(
        a, make_gold(steps, env), ecosystem="t", held_out=False, seconds=0.5
    )


def test_perfect_score_on_py_uv():
    s = score(
        "py-uv",
        [
            {"category": "install", "command": "uv sync"},
            {"category": "run", "command": "uv run py-uv-fixture"},
            {"category": "run", "command": "uv run python -V"},
        ],
    )
    assert (s.recall, s.precision, s.category_accuracy) == (1.0, 1.0, 1.0)
    assert s.missed == [] and s.extra == []


def test_recall_and_precision_are_computed_separately():
    s = score(
        "py-uv",
        [
            {"category": "install", "command": "uv sync"},
            {"category": "install", "command": "pip install flask"},  # never extracted
        ],
    )
    assert s.recall == 0.5
    assert s.precision == pytest.approx(1 / 3)  # 3 extracted, 1 matched
    assert s.missed == ["install: pip install flask"]
    assert s.extra == ["run: uv run py-uv-fixture", "run: uv run python -V"]


def test_category_accuracy_counts_text_matches_with_the_wrong_category():
    s = score(
        "py-uv", [{"category": "setup", "command": "uv sync"}]
    )  # extracted as install
    assert s.recall == 0.0 and s.category_accuracy == 0.0 and s.text_matched == 1


def test_test_commands_are_not_counted_in_precision():
    a = build_analysis(FIXTURES / "py-uv")
    gold = make_gold([{"category": "install", "command": "uv sync"}])
    base = score_repo(a, gold, ecosystem="t", held_out=False, seconds=0)
    assert (
        base.extracted == 3
    )  # install + two run commands; no test/lint commands exist


def test_empty_gold_gives_na_not_a_division_error():
    s = score("py-uv", [])
    assert s.recall is None and s.env_recall is None
    assert s.precision == 0.0 and s.extracted == 3


def test_env_recall_accepts_required_and_unknown_only():
    s = score("node-pnpm", [], env=["DATABASE_URL", "API_KEY", "PORT", "MISSING_VAR"])
    # DATABASE_URL and API_KEY are required; PORT is optional (has a default); MISSING_VAR absent
    assert (s.env_gold, s.env_found) == (4, 2)


def test_only_readme_commands_are_scored(tmp_path):
    (tmp_path / "Procfile").write_text("web: gunicorn app:app\n")
    (tmp_path / "README.md").write_text("# X\n\n```bash\nmake\n```\n")
    a = build_analysis(tmp_path)
    s = score_repo(
        a,
        make_gold([{"category": "build", "command": "make"}]),
        ecosystem="t",
        held_out=False,
        seconds=0,
    )
    assert s.extracted == 1 and s.recall == 1.0  # the Procfile command is not counted


def test_risk_flags_and_conflicts_are_reported():
    a = build_analysis(FIXTURES / "unix-only")
    s = score_repo(a, make_gold([]), ecosystem="t", held_out=False, seconds=0)
    assert s.risk_flags == 2  # `sudo apt install` and `curl ... | bash`


# --- the runner end to end --------------------------------------------------------------


def _mini(tmp_path):
    corpus = [
        {"name": "py-uv", "ecosystem": "python", "held_out": False},
        {"name": "node-pnpm", "ecosystem": "node", "held_out": True},
    ]
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "py-uv.yaml").write_text(
        yaml.safe_dump(
            {
                "repo": "py-uv", "variant": "native",
                "steps": [{"category": "install", "command": "uv sync"}],
                "runtimes": [], "env_required": [], "services": [],
            }
        )
    )  # fmt: skip
    (gold_dir / "node-pnpm.yaml").write_text(
        yaml.safe_dump(
            {
                "repo": "node-pnpm", "variant": "native",
                "steps": [{"category": "run", "command": "pnpm dev"}],
                "runtimes": [], "env_required": ["API_KEY"], "services": [],
            }
        )
    )  # fmt: skip
    return corpus, gold_dir


def test_run_bench_and_render(tmp_path):
    corpus, gold_dir = _mini(tmp_path)
    scores = run_bench(corpus, gold_dir, checkout=lambda repo: FIXTURES / repo["name"])
    assert [s.name for s in scores] == ["py-uv", "node-pnpm"]
    text = render(scores)
    assert "## Development set (per repo)" in text
    assert "## Held-out set (frozen: look at milestone ends only)" in text
    # the held-out repo is only in the held-out section
    dev_section = text.split("## Held-out set")[0]
    assert "node-pnpm" not in dev_section.split("## Misses and extras")[0]
    assert "100%" in text


def test_main_writes_a_results_file(tmp_path, capsys, monkeypatch):
    corpus, gold_dir = _mini(tmp_path)
    toml = tmp_path / "corpus.toml"
    toml.write_text(
        "\n".join(
            f'[[repo]]\nname = "{r["name"]}"\nurl = "https://example.invalid/{r["name"]}"\n'
            f'sha = "{"0" * 40}"\necosystem = "{r["ecosystem"]}"\nwhy = "t"\ne2e = false\n'
            f"held_out = {str(r['held_out']).lower()}\n"
            for r in corpus
        )
    )
    import run as run_module

    monkeypatch.setattr(
        run_module, "ensure_checkout", lambda repo, cache=None: FIXTURES / repo["name"]
    )
    monkeypatch.setattr(
        run_module,
        "run_bench",
        lambda c, g: run_bench(c, g, checkout=lambda repo: FIXTURES / repo["name"]),
    )
    out = tmp_path / "results"
    assert (
        main(["--corpus", str(toml), "--gold", str(gold_dir), "--out", str(out)]) == 0
    )
    files = list(out.glob("*.md"))
    assert len(files) == 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", files[0].name)
    assert "# rtfmup benchmark" in files[0].read_text(encoding="utf-8")
    assert "step recall" in capsys.readouterr().out


def test_no_held_out_flag_skips_those_repos(tmp_path, monkeypatch):
    corpus, gold_dir = _mini(tmp_path)
    toml = tmp_path / "corpus.toml"
    toml.write_text(
        "\n".join(
            f'[[repo]]\nname = "{r["name"]}"\nurl = "u"\nsha = "{"0" * 40}"\n'
            f'ecosystem = "e"\nwhy = "t"\ne2e = false\nheld_out = {str(r["held_out"]).lower()}\n'
            for r in corpus
        )
    )
    import run as run_module

    seen: list[str] = []

    def fake(c, g):
        seen.extend(r["name"] for r in c)
        return run_bench(c, g, checkout=lambda repo: FIXTURES / repo["name"])

    monkeypatch.setattr(run_module, "run_bench", fake)
    main(
        [
            "--corpus",
            str(toml),
            "--gold",
            str(gold_dir),
            "--out",
            str(tmp_path / "o"),
            "--no-held-out",
        ]
    )
    assert seen == ["py-uv"]


# --- plan order (T2.6) -----------------------------------------------------------------


def test_lcs():
    from metrics import lcs

    assert lcs([], []) == 0
    assert lcs(list("abcd"), list("acbd")) == 3
    assert lcs(list("abc"), list("xyz")) == 0
    assert lcs(list("abc"), list("abc")) == 3


def _gold(*steps: tuple[str, str]) -> Gold:
    return Gold(
        repo="x",
        variant="native",
        steps=[GoldStep(category=c, command=t) for c, t in steps],
        runtimes=[],
        env_required=[],
        services=[],
    )


def test_gold_env_copy_becomes_the_env_token():
    from metrics import ENV_TOKEN, gold_items

    gold = _gold(("install", "npm ci"), ("setup", "cp .env.example .env"))
    assert gold_items(gold) == [("npm ci", "install"), (ENV_TOKEN, "setup")]


def test_plan_order_scores_the_fixture_plan_against_its_gold(tmp_path):
    import shutil

    from hosts import windows_full
    from metrics import plan_order
    from run import plan_for

    root = tmp_path / "node-pnpm"
    shutil.copytree(FIXTURES / "node-pnpm", root)
    analysis = build_analysis(root)
    gold = _gold(
        ("install", "pnpm install"),
        ("setup", "cp .env.example .env"),  # the plan folds this into its env step
        ("run", "pnpm dev"),
    )
    plan, note = plan_for(analysis, gold, windows_full(), None)
    assert plan is not None and note == ""
    assert plan_order(analysis, plan, gold) == (3, 3)
    wrong_order = _gold(("run", "pnpm dev"), ("install", "pnpm install"))
    assert plan_order(analysis, plan, wrong_order) == (1, 2)


def test_plan_for_answers_questions_with_the_gold_variant(tmp_path):
    import shutil

    from hosts import windows_full
    from run import plan_for

    root = tmp_path / "docker-or-native"
    shutil.copytree(FIXTURES / "docker-or-native", root)
    analysis = build_analysis(root)
    plan, note = plan_for(
        analysis, _gold(("run", "python app.py")), windows_full(), None
    )
    assert plan is not None and plan.variant == "native" and note == ""


def test_gold_leaves_out_cloning_the_repo_itself():
    from metrics import gold_items

    own = "https://github.com/acme/widget"
    gold = _gold(
        ("install", "git clone https://github.com/acme/widget.git"),
        ("setup", "cd widget"),
        ("install", "npm ci"),
        ("install", "git clone https://github.com/acme/other.git"),
        ("install", "git clone https://github.com/acme/widget --depth 1 && cd widget"),
        ("setup", "cd elsewhere"),
    )
    assert gold_items(gold, own) == [
        ("npm ci", "install"),
        ("git clone https://github.com/acme/other.git", "install"),
        ("cd elsewhere", "setup"),
    ]
    assert len(gold_items(gold, None)) == 6  # without a URL nothing is dropped


def test_e2e_hands_its_report_path_to_the_workflow(tmp_path, monkeypatch):
    """The upload step must take this run's report only, not committed older ones."""
    from e2e import publish_report_path

    out = tmp_path / "gh_output"
    out.write_text("earlier=1\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    report = tmp_path / "results" / "e2e-2026-11-16-213134-ripgrep.md"
    publish_report_path(report)
    assert out.read_text(encoding="utf-8") == f"earlier=1\nreport={report}\n"


def test_e2e_report_path_is_a_no_op_off_ci(tmp_path, monkeypatch):
    from e2e import publish_report_path

    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    publish_report_path(tmp_path / "r.md")  # nothing to write to, nothing raised


def test_plan_order_scores_only_the_alternative_the_plan_chose():
    from metrics import _chosen_alternatives

    def entry(text, alt=None, cat="install"):
        return (text, cat), alt

    entries = [
        entry("winget install x", "get/winget"),
        entry("cargo install x", "get/cargo"),
        entry("curl -LO x.deb", "get/deb"),
        entry("sudo dpkg -i x.deb", "get/deb"),
        entry("x --version", cat="run"),
    ]
    planned = {("cargo install x", "install"), ("x --version", "run")}
    assert _chosen_alternatives(entries, planned) == [
        ("cargo install x", "install"),
        ("x --version", "run"),
    ]
    # nothing of the group planned: scored against the first option, README order
    assert _chosen_alternatives(entries, set()) == [
        ("winget install x", "install"),
        ("x --version", "run"),
    ]
    # a multi-step option wins when more of its steps are planned
    deb = {("curl -LO x.deb", "install"), ("sudo dpkg -i x.deb", "install")}
    assert _chosen_alternatives(entries, deb)[:2] == sorted(deb)


def test_gold_alt_must_be_group_slash_option():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        GoldStep(category="install", command="x", alt="nogroup")
    assert GoldStep(category="install", command="x", alt="get/cargo-install").alt


@pytest.mark.parametrize(
    ("platform", "sub"), [("win32", ""), ("linux", "bin"), ("darwin", "bin")]
)
def test_on_host_npm_globals_are_on_path_for_each_os(
    tmp_path, monkeypatch, platform, sub
):
    """T5.8: on macOS `npm install pm2 -g && pm2 ...` hit `pm2: command not found`."""
    import e2e

    monkeypatch.setattr(e2e.sys, "platform", platform)
    env = e2e.step_env(tmp_path, on_host=True)
    first = env["PATH"].split(e2e.os.pathsep)[0]
    assert first == str(
        tmp_path / "npm-global" / sub if sub else tmp_path / "npm-global"
    )
    assert env["NPM_CONFIG_PREFIX"] == str(tmp_path / "npm-global")
