# Real-repo benchmark

Measures the README extractor (Pass 2) against person-reviewed gold annotations on ten
real repositories, and (M3) whether `rtfmup up` gets the e2e repos running. Not shipped in
the wheel. The analysis bench (`run.py`) is read-only: nothing from the corpus is executed.
The e2e bench (`e2e.py`) executes the corpus's setup steps; see below.

```console
uv run python bench/fetch.py            # fetch the pinned commits into the cache dir
uv run python bench/run.py              # analyze all ten, print the tables, write results/<date>.md
uv run python bench/run.py --only flask # a subset
uv run python bench/run.py --no-held-out
gh workflow run bench-e2e.yml -R xb17/rtfmup                    # e2e, every e2e repo
gh workflow run bench-e2e.yml -R xb17/rtfmup -f repos="flask"   # e2e, a subset
```

## Files

| File | What |
|---|---|
| `corpus.toml` | the ten repos: `url`, pinned `sha`, `ecosystem`, `e2e`, `held_out`, plus the e2e answers (`e2e_variant`, `e2e_run`), CLI done checks (`e2e_done`) and `fetch_tags` |
| `gold/<name>.yaml` | the gold annotation for one repo |
| `fetch.py` | shallow-fetches exactly the pinned sha into `%LOCALAPPDATA%\rtfmup\Cache\bench` (never into this repo), with an `origin` remote, and tags when `fetch_tags` is set |
| `metrics.py` | normalization and scoring (pure functions) |
| `run.py` | runs the analysis, prints and writes the report |
| `e2e.py` | runs `rtfmup up --yes` on one e2e repo and writes `results/e2e-<date>-<time>-<repo>.md` |
| `results/` | one Markdown report per run; every e2e attempt is kept |

## E2E (M3, T3.8)

`e2e.py` **executes third-party setup steps**, so it runs on an ephemeral GitHub `windows-latest`
runner, one fresh VM per repo (`.github/workflows/bench-e2e.yml`, manual trigger). Locally it
refuses unless given `--on-host`. Redirecting npm's global prefix is not a sandbox: on
2026-11-14 the uptime-kuma README's `pm2 install` left a pm2 daemon running and `~/.pm2` in the
home directory of the machine it ran on.

A run copies the pinned checkout into a scratch folder named after the repo, takes rtfmup's dev
venv off PATH (users run rtfmup from uvx or pipx), and runs `rtfmup up --yes` with the corpus
answers and stdin closed, so high-risk steps are declined. Success is PLAN §4.2's: the health
check passes, or for a CLI with no port, `up` finishes and `e2e_done` succeeds. Answering
rtfmup's `.env` prompts is not possible yet: `--yes` turns them off, so a repo with required
values (laravel) fails at the env step.

## Gold format

```yaml
repo: express            # corpus name
variant: native          # "native" unless the repo is Docker-only
steps:                   # the setup steps a competent developer takes from the README alone
  - {category: install, command: "npm install", variant: native, platform: any}
runtimes: [{name: node, spec: ">=18"}]
env_required: [APP_KEY]  # variables the developer must set
services: []
notes: ...
```

Gold is what a developer sees: the **README commands** plus the env templates (`.env.example` and
like). A real `.env` is never read (known gap: `full-stack-fastapi-template` keeps its variables
only in a committed `.env`). A repo whose README has no commands has `steps: []`, and any command
rtfmup reports from it counts against precision. Test and lint commands are not gold steps.
Each `(command, category)` pair appears once.

## Metrics

Commands are normalized (whitespace collapsed, prompt stripped, trailing `;` dropped,
case-sensitive). Only README-sourced extracted commands count (CI and Procfile commands do not).

| Metric | Definition |
|---|---|
| step recall | gold steps matched by an extracted command with the same text **and** category, divided by gold steps |
| precision | extracted install/setup/build/run commands that match a gold step, divided by all of them |
| category accuracy | among gold steps whose text was extracted, the share with the same category |
| env recall | gold required variables found with `required` in `{required, unknown}`, divided by gold required variables |
| plan order (M2) | longest common subsequence between the plan's steps and the gold steps, divided by the gold steps compared. Matches on README text and category; the plan's single `env` step matches a gold `cp .env.example .env`. Gold steps that clone the repo itself, and the `cd` after it, are left out of both sides: rtfmup starts inside the repo. The plan is built for the gold variant, with other questions answered by their first option (listed under "Plan notes"). |

`n/a` means the denominator is zero (for example a README with no gold steps). Aggregates are
micro-averaged (totals summed over repos, then divided). The report also lists risk flags
(`sudo`, `pipe_to_shell`), conflicts and questions per repo, and the missed and extra commands.

## Held-out set

`datasette`, `ripgrep` and `laravel` are frozen. Don't tune rules on the whole corpus and then
report on the same set: tune on the other seven, and look at the held-out numbers only at
milestone ends. `run.py` prints them in a separate table. The same holds for e2e: a held-out
e2e failure is reported, not fixed by a rule written for it.

## Status

Corpus and gold approved by Drew. Known gaps, deliberately not tuned for: commands in Markdown
table cells (`fzf`) and a committed `.env` used as a template. The `datasette` held-out result
is contaminated: the indented-block rule was added after its README was read.

Plan order is capped by how the gold is written: for `fzf` and `uptime-kuma` the gold lists
every install path the README offers, but a plan follows one. The plan also orders by kind
(install, env, setup, build, run) rather than README order, which costs a little where a
README interleaves them.
