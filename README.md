# rtfmup

**Set up any repo from its README.**
Read The Fine Manual, then bring it `up`.

> ⚠️ **Early development, not ready for use.** This release only claims the name and ships a minimal `scan` command.

## What it will do

`rtfmup` takes a Git repo (a URL or a local path), works out what it is, and gets it running on your machine, whether or not you know its language, package manager, or runtime. Dependency manifests describe *what* a project needs but not the full path from clone to running. That path usually lives in the README. rtfmup reads manifests as ground truth and parses the README's structure (headings, fenced shell blocks, env vars, ports) as data. No LLM is required. It turns both into a reviewed, step-by-step plan: runtime → dependencies → environment → run. Nothing runs without your confirmation.

## Install

```console
uvx rtfmup --version
```

## Usage today

```console
rtfmup --version
rtfmup scan [path]          # detect manifests in a local repo (default: .)
rtfmup scan [path] --json   # same, as JSON
```

`scan` currently recognises `pyproject.toml`, `requirements.txt`, `package.json`, `Cargo.toml`, `go.mod`, `Dockerfile`, Compose files, and `.env.example` at the repo root.

## Planned CLI

```console
rtfmup <url|path>   # full flow: analyse, show plan, confirm, set up, run
rtfmup scan <path>  # analysis only (manifests + README), as summary or JSON
rtfmup plan <path>  # show the execution plan without running anything
```

## Roadmap

1. **Detection engine:** manifest scanner, README Markdown-AST parser, command classification (install / setup / run).
2. **Plan generator:** an ordered plan with gaps surfaced (missing runtimes, env values, external services).
3. **Executor (Windows first):** invokes existing tools (`uv`, `npm`, `cargo`, …), guides `.env` setup, runs the project, and health-checks it.
4. **Polish:** recovery from common failures and clearer diagnostics.

**Platform:** Windows only for now. Linux and macOS support is planned for later versions.

Later: Linux and macOS support, deployment-file awareness, a learning mode that writes the spec the repo was missing, and an opt-in local-LLM assist.

## License

[Apache-2.0](LICENSE)
