# rtfmup

**Set up any repo from its README.**
Read The Fine Manual, then bring it `up`.

> ⚠️ **Early development.** This release analyses a repo, builds a setup plan, exports a devcontainer, and on Windows sets the repo up and runs it with `rtfmup up`. Many real READMEs still trip it up: in our benchmark, `up` brings 1 of 7 real repos all the way to running. `recipe` is a stub.

## What it does

`rtfmup` takes a Git repo (a URL or a local path), works out what it is, and gets it running on your machine, whether or not you know its language, package manager, or runtime. Dependency manifests describe *what* a project needs but not the full path from clone to running. That path usually lives in the README. rtfmup reads manifests as ground truth and parses the README's structure (headings, fenced shell blocks, env vars, ports) as data. No LLM is required. It turns both into a reviewed, step-by-step plan: runtime → dependencies → environment → run. Nothing runs without your confirmation.

## Install

```console
uvx rtfmup --version
```

## Usage

```console
rtfmup --version
rtfmup up <path|url>            # analyse, show the plan, confirm, set it up, run it (Windows)
rtfmup scan [path|url]          # analyse a repo (default: .); a URL is cloned into the cache dir
rtfmup scan [path|url] --json   # the same analysis as JSON (schema: rtfmup schema)
rtfmup scan [path|url] --fresh  # ignore the cached analysis
rtfmup plan [path|url]          # show the ordered setup plan, with risk levels and gaps; runs nothing
rtfmup plan [path|url] --json   # the same plan as JSON (also saved to .rtfmup/plan.json)
rtfmup schema [--markdown]       # print the analysis JSON Schema, or the same as Markdown (docs/schema.md)
rtfmup doctor                   # probe this machine: OS, shells, installed tools (read-only)
rtfmup export devcontainer [path|url] [--write]   # print a devcontainer.json; --write saves it to .devcontainer/ after asking
```

`plan` and `up` accept `--variant` (for example native or docker), `--component` (multi-component repos) and `--run` (pick the run command when README and manifests differ).

`up` shows every step with its source and risk before running anything. `--yes` approves low- and medium-risk steps; high-risk ones (elevation, `sudo`, piping a download into a shell) always need you to type `yes`. It installs missing runtimes with `uv`, Scoop, winget or Chocolatey, creates `.env` from the repo's template and asks for required values (secrets are never stored), runs the project, and waits for its port. A runtime it cannot install, or a step for another OS, is reported as a gap, not guessed around.

`up --resume` continues the last run: steps that finished show as "already done" and the rest run again through the usual confirmation. It refuses, runs nothing and exits 2 if the plan has changed since (a README or manifest edit, another variant or host). `--quiet` (up) keeps the stage headers, the confirmation and the final line, and shows only a failing step's last 40 lines. `--verbose` (up, plan) adds the host probe table and the analysis summary before the plan. When a step fails, rtfmup prints a diagnosis and fix for the failures it recognises (missing C++ build tools, a busy port, Docker not running, wrong runtime version, long paths, PowerShell execution policy, locked files).

When a README lists several ways to install the project itself (a package manager, `cargo install`, a source build, a release download), those are alternatives. The plan keeps one, and lists the others under "Alternatives not chosen".

### Exit codes

| Code | Meaning | Detail |
| --- | --- | --- |
| 0 | ok | Finished: the project is up, or setup completed. |
| 1 | step failed | A step failed, or nothing could be run. |
| 2 | usage | Bad arguments, an unanswered question, `--resume` refused, or an OS `up` does not support yet. |
| 3 | declined | You declined the confirmation, or the run step. |
| 4 | gaps block | A gap makes the plan impossible (a missing runtime with no installer, steps for another OS only, a directory that is not there). Nothing was run. |

`scan` is read-only. It reads manifests (Python, Node, Rust, Go, Ruby, PHP, .NET, Docker and Compose,
env templates, Makefile/Justfile, devcontainer, CI, deployment files), reads the README
(`.md` and `.rst`), classifies the commands it finds, and reports conflicts between the two and
questions it would have to ask. It never runs anything from the repo.

The analysis format is documented in [docs/schema.md](docs/schema.md). Changes between releases are in [CHANGELOG.md](CHANGELOG.md).

## Roadmap

1. **Detection engine** (done): manifest scanner, README Markdown-AST parser, command classification (install / setup / run).
2. **Plan generator** (done): an ordered plan with gaps surfaced (missing runtimes, env values, external services).
3. **Executor, Windows** (done): invokes existing tools (`uv`, `npm`, `cargo`, …), guides `.env` setup, runs the project, and health-checks it.
4. **Polish** (in progress): install alternatives, failure diagnostics, resume, clearer exit codes.

**Platform:** Windows first.

## License

[Apache-2.0](LICENSE)
