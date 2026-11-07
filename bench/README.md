# Real-repo benchmark

Measures the README extractor (Pass 2) against person-reviewed gold annotations on ten
real repositories. Not shipped in the wheel. Read-only: nothing from the corpus is executed.

```console
uv run python bench/fetch.py            # fetch the pinned commits into the cache dir
uv run python bench/run.py              # analyze all ten, print the tables, write results/<date>.md
uv run python bench/run.py --only flask # a subset
uv run python bench/run.py --no-held-out
```

## Files

| File | What |
|---|---|
| `corpus.toml` | the ten repos: `url`, pinned `sha`, `ecosystem`, `e2e`, `held_out` |
| `gold/<name>.yaml` | the gold annotation for one repo |
| `fetch.py` | shallow-fetches exactly the pinned sha into `%LOCALAPPDATA%\rtfmup\Cache\bench` (never into this repo) |
| `metrics.py` | normalization and scoring (pure functions) |
| `run.py` | runs the analysis, prints and writes the report |
| `results/` | one Markdown report per run |

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

`n/a` means the denominator is zero (for example a README with no gold steps). Aggregates are
micro-averaged (totals summed over repos, then divided). The report also lists risk flags
(`sudo`, `pipe_to_shell`), conflicts and questions per repo, and the missed and extra commands.

## Held-out set

`datasette`, `ripgrep` and `laravel` are frozen. Don't tune rules on the whole corpus and then
report on the same set: tune on the other seven, and look at the held-out numbers only at
milestone ends. `run.py` prints them in a separate table.

## Status

Corpus and gold approved by Drew. Known gaps, deliberately not tuned for: commands in Markdown
table cells (`fzf`) and a committed `.env` used as a template. The `datasette` held-out result
is contaminated: the indented-block rule was added after its README was read.
