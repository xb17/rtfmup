# rtfmup

**Set up any repo from its README.**
Read The Fine Manual, then bring it `up`.

> ⚠️ **Early development.** This release analyses a repo, builds a setup plan, and can export a devcontainer. It does not set anything up yet: `up` and `recipe` are stubs.

## What it will do

`rtfmup` takes a Git repo (a URL or a local path), works out what it is, and gets it running on your machine, whether or not you know its language, package manager, or runtime. Dependency manifests describe *what* a project needs but not the full path from clone to running. That path usually lives in the README. rtfmup reads manifests as ground truth and parses the README's structure (headings, fenced shell blocks, env vars, ports) as data. No LLM is required. It turns both into a reviewed, step-by-step plan: runtime → dependencies → environment → run. Nothing runs without your confirmation.

## Install

```console
uvx rtfmup --version
```

## Usage today

```console
rtfmup --version
rtfmup scan [path|url]          # analyse a repo (default: .); a URL is cloned into the cache dir
rtfmup scan [path|url] --json   # the same analysis as JSON (schema: rtfmup schema)
rtfmup scan [path|url] --fresh  # ignore the cached analysis
rtfmup plan [path|url]          # show the ordered setup plan, with risk levels and gaps; runs nothing
rtfmup plan [path|url] --json   # the same plan as JSON (also saved to .rtfmup/plan.json)
rtfmup doctor                   # probe this machine: OS, shells, installed tools (read-only)
rtfmup export devcontainer [path|url] [--write]   # print a devcontainer.json; --write saves it to .devcontainer/ after asking
```

`plan` accepts `--variant` (for example native or docker), `--component` (multi-component repos) and `--run` (pick the run command when README and manifests differ).

`scan` is read-only. It reads manifests (Python, Node, Rust, Go, Ruby, PHP, .NET, Docker and Compose,
env templates, Makefile/Justfile, devcontainer, CI, deployment files), reads the README
(`.md` and `.rst`), classifies the commands it finds, and reports conflicts between the two and
questions it would have to ask. It never runs anything from the repo.

## Planned CLI

```console
rtfmup <url|path>   # full flow: analyse, show plan, confirm, set up, run
rtfmup scan <path>  # analysis only (manifests + README), as summary or JSON
rtfmup plan <path>  # show the execution plan without running anything (available now)
```

## Roadmap

1. **Detection engine:** manifest scanner, README Markdown-AST parser, command classification (install / setup / run).
2. **Plan generator:** an ordered plan with gaps surfaced (missing runtimes, env values, external services).
3. **Executor (Windows first):** invokes existing tools (`uv`, `npm`, `cargo`, …), guides `.env` setup, runs the project, and health-checks it.
4. **Polish:** recovery from common failures and clearer diagnostics.

**Platform:** Windows first.

## License

[Apache-2.0](LICENSE)
