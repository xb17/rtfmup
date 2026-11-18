# rtfmup

**Set up any repo from its README.**
Read The Fine Manual, then bring it `up`.

> ⚠️ **Early development.** This release analyses a repo, builds a setup plan, exports a devcontainer, and sets the repo up and runs it with `rtfmup up` on Windows, Linux and macOS. Many real READMEs still trip it up: in our benchmark, `up` brings 2 of 7 real repos all the way to running. `recipe` is a stub.

## What it does

`rtfmup` takes a Git repo (a URL or a local path), works out what it is, and gets it running on your machine, whether or not you know its language, package manager, or runtime. Dependency manifests describe *what* a project needs but not the full path from clone to running. That path usually lives in the README. rtfmup reads manifests as ground truth and parses the README's structure (headings, fenced shell blocks, env vars, ports) as data. No LLM is required. It turns both into a reviewed, step-by-step plan: runtime → dependencies → environment → run. Nothing runs without your confirmation.

## Install

```console
uvx rtfmup --version
```

## Usage

```console
rtfmup --version
rtfmup up <path|url>            # analyse, show the plan, confirm, set it up, run it
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

`up` shows every step with its source and risk before running anything. `--yes` approves low- and medium-risk steps; high-risk ones (elevation, `sudo`, piping a download into a shell) always need you to type `yes`. It installs missing runtimes with the first of these that is already installed, and never installs a package manager itself: `uv` and mise everywhere, then Scoop, winget or Chocolatey on Windows, Homebrew on macOS, or apt, dnf or pacman on Linux (apt, dnf and pacman need `sudo`, so they always ask you to type `yes`). creates `.env` from the repo's template and asks for required values (secrets are never stored), runs the project, and waits for its port. A runtime it cannot install, or a step for another OS, is reported as a gap, not guessed around.

`up --resume` continues the last run: steps that finished show as "already done" and the rest run again through the usual confirmation. It refuses, runs nothing and exits 2 if the plan has changed since (a README or manifest edit, another variant or host). `--quiet` (up) keeps the stage headers, the confirmation and the final line, and shows only a failing step's last 40 lines. `--verbose` (up, plan) adds the host probe table and the analysis summary before the plan. When a step fails, rtfmup prints a diagnosis and fix for the failures it recognises (missing C++ build tools, a busy port, Docker not running, wrong runtime version, long paths, PowerShell execution policy, locked files).

When a README lists several ways to install the project itself (a package manager, `cargo install`, a source build, a release download), those are alternatives. The plan keeps one, and lists the others under "Alternatives not chosen".

### Exit codes

| Code | Meaning | Detail |
| --- | --- | --- |
| 0 | ok | Finished: the project is up, or setup completed. |
| 1 | step failed | A step failed, or nothing could be run. |
| 2 | usage | Bad arguments, an unanswered question, or `--resume` refused. |
| 3 | declined | You declined the confirmation, or the run step. |
| 4 | gaps block | A gap makes the plan impossible (a missing runtime with no installer, steps for another OS only, a directory that is not there). Nothing was run. |

`scan` is read-only. It reads manifests (Python, Node, Rust, Go, Ruby, PHP, .NET, Docker and Compose,
env templates, Makefile/Justfile, devcontainer, CI, deployment files), reads the README
(`.md` and `.rst`), classifies the commands it finds, and reports conflicts between the two and
questions it would have to ask. It never runs anything from the repo.

The analysis format is documented in [docs/schema.md](docs/schema.md). Changes between releases are in [CHANGELOG.md](CHANGELOG.md).

## Platforms

`scan`, `plan`, `doctor` and `export devcontainer` are pure Python and run anywhere Python 3.11 or newer does. `up` runs the plan on the host it is on. A step written for another OS is listed in the plan as a gap and never run; there is no WSL fallback.

| OS | Arch | Shell for steps that need one | Tested by |
|---|---|---|---|
| Windows 11 | x86_64 | PowerShell 7 (`pwsh`) | every push on a Windows 11 machine; GitHub `windows-latest` at each release (Windows 10 is not tested) |
| Linux (Ubuntu 24.04 container) | x86_64 | `bash -c` | every push; GitHub `ubuntu-latest` at each release |
| macOS 27 (Apple silicon) | arm64 | `/bin/bash` (3.2) | every push on a Mac mini |
| macOS 26 (Apple silicon) | arm64 | `/bin/bash` (3.2) | GitHub `macos-latest` at each release |
| macOS 26 (Intel) | x86_64 | `/bin/bash` (3.2) | GitHub `macos-26-intel` at each release |

What the tests cover: the unit and plan tests on all of the rows above, and `rtfmup up` end to end on a synthetic fixture (it reaches `Up:` and Ctrl+C leaves nothing running). Real installs through a package manager are tested on one platform each: Winget, Scoop, Chocolatey and `uv` on GitHub `windows-latest`; apt in a throwaway Ubuntu 24.04 container as root; mise and `uv python install` on the Mac mini as a non-admin user; Homebrew on GitHub `macos-latest` at milestone ends.

What they do not cover: Linux on arm64 has no job at all. dnf and pacman steps are generated from package tables checked against the Fedora and Arch package indexes, and are never run. The Debian and Ubuntu table is checked on Ubuntu 24.04 and 26.04, not on Debian itself. Real-repo end-to-end results (the benchmark below) are measured on Windows only.

## Benchmark

Measured on a pinned corpus of 10 real repos (`bench/`), 2026-11-18:

| Measure | All 10 repos | Held-out 3 |
|---|---|---|
| README command recall | 66% | 94% |
| README command precision | 61% | 91% |
| Category accuracy | 90% | 94% |

**End to end:** `rtfmup up --yes` on a fresh Windows VM per repo brings **2 of 7** to running (uptime-kuma, ripgrep). The tuning set is 1 of 4 and the held-out set 1 of 3. ripgrep has been tuned on, so the held-out figure overstates unseen repos. Per-repo reports are in `bench/results/`.

## Roadmap

1. **Detection engine** (done): manifest scanner, README Markdown-AST parser, command classification (install / setup / run).
2. **Plan generator** (done): an ordered plan with gaps surfaced (missing runtimes, env values, external services).
3. **Executor, Windows** (done): invokes existing tools (`uv`, `npm`, `cargo`, …), guides `.env` setup, runs the project, and health-checks it.
4. **Polish** (done): install alternatives, failure diagnostics, resume, clearer exit codes.

5. **Linux and macOS** (unreleased): the same flow on both, with their own shells, package managers (Homebrew, apt, dnf, pacman, mise), PATH handling and failure diagnostics. See Platforms for what is tested where.

## License

[Apache-2.0](LICENSE)
