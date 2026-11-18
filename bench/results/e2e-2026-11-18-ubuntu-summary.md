# e2e bench on GitHub Linux and macOS runners, 2026-11-18 (M5, T5.8)

The same harness and rules as the Windows runs: `bench-e2e.yml` with the new `os` input,
one fresh VM per repo, `rtfmup up --yes` with stdin closed (high-risk steps declined),
and answers from `corpus.toml`. rtfmup at GitHub `4426b11` (the filtered copy of Gitea
`b15e84d`). Runs 44347737177 (`ubuntu-latest`) and 44347742532 (`macos-latest`, arm64).
On macOS, fzf and express were cancelled before they started, because no runner picked
them up within about 22 minutes, and were re-run in the same workflow run.

GitHub's images ship common runtimes (node, Python, Go, Ruby; Rust on Linux), so these are
the figures that compare with the Windows 2 of 7. Per-repo reports are in
`ubuntu-latest/` and `macos-latest/`.

**E2E success: Linux 1 of 7, macOS 2 of 7** (Windows 2 of 7, same day).

| repo | set | ubuntu-latest | macos-latest |
|---|---|---|---|
| uptime-kuma | tuning | blocked: Node >= 26.2.0 needed, image has 24.22.3 | **SUCCESS**, `Up: :3001` |
| express | tuning | run step `express /tmp/foo` declined (high risk) | same |
| flask | tuning | `flask run`: no app (README) | same |
| fzf | tuning | blocked: `.tool-versions` Ruby 3.4, image has 3.3.8 | `export FZF_DEFAULT_OPTS=...` shell snippet fails |
| datasette | held-out | `datasette serve path/to/database.db`: placeholder | same |
| ripgrep | held-out* | **SUCCESS**, `rg --version` | **SUCCESS** |
| laravel | held-out | stops at `.env`: APP_KEY and AWS keys required, stdin closed | blocked: composer not installed |

\* tuned on since 2026-11-16.

On our own hosts, which are barer (see `e2e-2026-11-18-linux-macos-summary.md`), the Mac
mini as a non-admin user is 1 of 7 and a bare `ubuntu:26.04` container is 0 of 7.
