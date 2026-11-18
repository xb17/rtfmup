# e2e bench, 2026-11-18 (M5, T5.8, first run on Linux and macOS)

Same harness as Windows (`bench/e2e.py`): `rtfmup up --yes` with stdin closed, so high-risk
steps are declined, and answers come from `corpus.toml`. rtfmup at `6afb7a9` to `8d18b3d`,
which differ only in the bench drivers.

- **Linux:** `bench/e2e-linux.sh`, one `docker run --rm ubuntu:26.04` per repo on Drew's
  machine, as root. Only git, curl and uv are added. Reports are in `linux/`.
- **macOS:** `bench/e2e-macos.sh` on the Mac mini (macOS 27.2, arm64), as the non-admin
  `gitea-runner` user over SSH. uv is installed into the run's temp dir. Reports are in `macos/`.

**E2E success: Linux 0 of 7, macOS 0 of 7** (Windows on GitHub runners: 2 of 7, same day).

The two hosts are much barer than GitHub's Windows image, which ships node, Python, Go, Rust
and PHP. On Linux every runtime has to come from apt, which is high risk and therefore
declined. On the Mac mini the runner user can't write to Drew's Homebrew prefix. So these
numbers measure "what rtfmup can do from a bare host with high risk declined", not the same
thing as the Windows figure.

## Linux (bare Ubuntu 26.04 container)

| repo | where it stopped | kind |
|---|---|---|
| datasette | `npm ci` can't start: node via apt was declined (high risk) | host (bare) |
| flask | `flask run`: no app (README's app is a "save as app.py" block) | README, same as Windows |
| ripgrep | planned `sudo emerge sys-apps/ripgrep` (Gentoo) on Ubuntu, declined | **rtfmup bug 1** |
| fzf | blocked: Go not installed, no provider without high risk | host (bare) |
| express | `npm install express` can't start: node via apt declined | host (bare) |
| uptime-kuma | blocked: Node >= 26.2.0 needed, apt's node is older, no mise | host (bare) |
| laravel | blocked: composer not installed | host (bare) |

## macOS (Mac mini, non-admin user)

| repo | where it stopped | kind |
|---|---|---|
| datasette | `brew install datasette`: permission denied on `/opt/homebrew` (owned by drew) | **rtfmup bug 2** |
| flask | port 5000 already in use (on macOS that is normally AirPlay Receiver); the app is missing anyway | host + README |
| ripgrep | `brew install ripgrep`: permission denied, as datasette | **rtfmup bug 2** |
| fzf | blocked: `.tool-versions` wants Ruby 3.4, only the system 2.6.10 | host |
| express | `express /tmp/foo && cd /tmp/foo` declined (high risk), exit 3 | same as Windows |
| uptime-kuma | `npm install pm2 -g && pm2 ...`: `pm2: command not found` | **bench bug 3** |
| laravel | blocked: composer not installed | host |

Left behind on the Mac mini after the run: nothing (processes, launch agents, home directory
and global npm compared before and after). The remote work dir was deleted afterwards.

## Bugs found

1. **Unprobed system managers win ties.** The probe knows `apt` but not `apt-get` or `emerge`,
   so both are "not probed" and README order picks Gentoo's `emerge` on Ubuntu. The same tie
   lets an unprobed system manager outrank `cargo install`. Fix: probe each system manager
   name, or treat an unprobed one as missing on Linux and macOS.
2. **brew is chosen when its prefix isn't writable.** On a multi-user Mac, brew is found but
   the user can't install. Fix: the probe records whether the brew prefix is writable, and an
   unwritable brew is not used (a gap that says why, like a missing tool).
3. **The bench's npm prefix on posix.** `bench/e2e.py` sends `npm -g` into a scratch prefix and
   puts that prefix on PATH, but on Linux and macOS global binaries go to `<prefix>/bin`.
   This is a harness bug, not rtfmup. Rerunning uptime-kuma after the fix will start a pm2
   daemon on the Mac mini, the same leftover risk as 2026-11-15, so it needs Drew's go-ahead.
