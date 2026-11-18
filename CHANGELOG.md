# Changelog

All notable changes to rtfmup are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may change
behavior).

## [Unreleased]

Linux and macOS (M5).

### Added

- `up` runs on Linux and macOS. Steps that need a shell run through `bash -c` (macOS
  uses the system `/bin/bash`, version 3.2), and Ctrl+C stops the step's whole process
  tree. The "not yet supported on this OS" stop is gone.
- `doctor` reports the Linux distribution (from `/etc/os-release`), the macOS version,
  the Homebrew prefix, Command Line Tools and Rosetta status, and whether it runs as root.
- Runtime providers for macOS and Linux: Homebrew, apt, dnf, pacman and mise (mise on
  all three OSes, Windows included). Order per OS: `uv` and mise first, then the OS's
  own manager. apt, dnf and pacman steps use `sudo` only when not root and always ask
  you to type `yes`.
- After a runtime step, the next steps find what it installed: Homebrew, mise shims,
  `~/.cargo/bin`, `~/.local/bin` and Go's bin directory are put on the child PATH (no
  shell profile is edited).
- A pinned Node version with no macOS arm64 build (below 16) is reported as a gap
  instead of failing during the install.
- The port-in-use gap names the owning process on Linux and macOS.
- `up` prints a diagnosis for a missing Xcode Command Line Tools, a missing Rosetta,
  PEP 668 "externally managed" Pythons, missing C compilers, global npm `EACCES` and
  Homebrew not on PATH.

### Changed

- The classifier tags commands for one OS: `apt`, `dnf`, `pacman`, `systemctl` and the
  like are `linux`, `brew`, `xcode-select` and the like are `macos`, and generic bash
  stays `unix`. A macOS host no longer plans a README's `apt` line.
- README "Platforms" lists what is tested where.

## [0.4.0] - 2026-11-18

Polish (M4).

### Added

- `plan` and `up` pick one install route when a README lists several ways to get the
  project itself (a package manager, `cargo install`, `cargo binstall`, a source build,
  a release download). The others are listed under "Alternatives not chosen"
  (`Plan.skipped_alternatives` in `plan.json`).
- `up` prints a diagnosis and a fix after a failing step for known failures: missing
  C++ build tools, no Rust linker, a busy port, Docker not running, wrong runtime
  version, long paths, PowerShell execution policy, locked files.
- `up --resume` continues from the first step that did not finish, when the plan is
  unchanged.
- `--quiet` (up) and `--verbose` (up, plan).
- `rtfmup schema --markdown` and `docs/schema.md`.
- The host probe finds `cargo-binstall`.

### Changed

- Exit codes are defined in one module and documented in the README: 0 ok, 1 step
  failed, 2 usage, 3 declined, 4 gaps block. A blocked `up` now exits 4 (it was 1).
- `cargo binstall` is rated medium risk, like `cargo install`.

## [0.3.0] - 2026-11-16

### Added

- `rtfmup up` on Windows: confirmation (one question for low and medium risk steps,
  high risk steps ask one by one and are never covered by `--yes`), a step runner that
  streams output and stops the whole process tree on Ctrl+C, a redacted `run.log`, and
  `.rtfmup/state.json`.
- Runtime providers: `uv`, Scoop, winget, Chocolatey, and a manual fallback.
- `.env` setup from the repo's template, asking for required values without storing
  secrets.
- Run step and health check: waits for the project's port and reports `Up: <url>`; a
  port already in use is a gap.
- Opt-in end-to-end tests on synthetic fixtures; a real-repo end-to-end benchmark that
  runs on ephemeral GitHub runners only.

### Changed

- README alternatives for system package managers: one manager per section, and a
  missing winget, Scoop or Chocolatey is a gap.
- A `cd` into a directory that is not in the repo blocks `up` (a `working_directory`
  gap), but only when a planned command follows it.
- README `pip` commands run inside the project's virtual environment, never the host
  Python.
- Version probes that time out are retried once with a longer limit.

## [0.2.0] - 2026-11-14

### Added

- `rtfmup plan`: an ordered setup plan (install, env, setup, build, run, health check)
  with risk levels and gaps, saved to `.rtfmup/plan.json`.
- `rtfmup doctor`: probes the host's OS and installed tools, read-only.
- `rtfmup export devcontainer`: writes `.rtfmup/devcontainer.json`; `--write` saves it
  to `.devcontainer/` after asking and never overwrites.
- Risk rules (low, medium, high) and gap detection (missing runtimes, versions,
  environment values, accounts, services, other-OS steps).
- A plan-order metric in the benchmark.

## [0.1.0] - 2026-11-07

### Added

- `rtfmup scan <path|url> [--json] [--fresh]`: manifest detection (Python, Node, Rust,
  Go, Ruby, PHP, .NET, Docker and Compose, env templates, task runners, devcontainer,
  CI, deployment files) plus README extraction (Markdown and reStructuredText):
  commands, versions, environment variables, ports and accounts, classified by
  category, variant, platform and risk flags.
- Conflicts between README and manifests, and the questions rtfmup would have to ask.
- The analysis schema (v1) and `rtfmup schema`.
- Analysis caching keyed on the git HEAD, and URL targets cloned into the cache
  directory.
- A real-repo benchmark harness with person-reviewed gold annotations.

## [0.0.1.dev0] - 2026-11-06

### Added

- Name claim: a minimal manifest scan of the repo root.

[Unreleased]: https://github.com/xb17/rtfmup/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/xb17/rtfmup/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/xb17/rtfmup/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/xb17/rtfmup/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/xb17/rtfmup/compare/v0.0.1.dev0...v0.1.0
[0.0.1.dev0]: https://github.com/xb17/rtfmup/releases/tag/v0.0.1.dev0
