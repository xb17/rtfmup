#!/usr/bin/env bash
# T5.8, macOS: the e2e bench on the Mac mini, as the runner's dedicated non-admin
# user over SSH (PLAN T5.8; every session is a section 5 stop, approved by Drew).
# EXECUTES third-party code there. Unlike a container, the host persists, so this
# snapshots the user's processes, launch agents and home directory before and
# after, and reports what is left behind.
#
#   bash bench/e2e-macos.sh                # every e2e repo
#   bash bench/e2e-macos.sh flask ripgrep  # just these
#
# Key auth is Drew's setup (his public key in gitea-runner's authorized_keys);
# this script never handles a key.
set -euo pipefail

SSH_HOST="${SSH_HOST:-gitea-runner@macmini.local}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${OUT:-$HERE/results}"
mkdir -p "$OUT"

if [ "$#" -gt 0 ]; then
  repos=("$@")
else
  mapfile -t repos < <(cd "$ROOT" && uv run python -c "
import tomllib
for r in tomllib.load(open('bench/corpus.toml', 'rb'))['repo']:
    if r.get('e2e'):
        print(r['name'])" | tr -d '\r')  # Windows Python prints CRLF
fi

remote() { ssh -o BatchMode=yes "$SSH_HOST" "$@"; }

echo "== host: $(remote 'echo "$(whoami)@$(hostname -s), macOS $(sw_vers -productVersion) $(uname -m)"')"
[ "$(remote whoami)" = "gitea-runner" ] || { echo "FAILED: not the gitea-runner user"; exit 1; }

# What the user owns before: processes, launch agents, top-level home entries.
snapshot='
  ps -U "$(id -u)" -o comm= | sed "s#.*/##" | sort -u
  echo "--- launch agents"; ls "$HOME/Library/LaunchAgents" 2>/dev/null
  echo "--- home"; ls -A "$HOME"
  echo "--- npm global"; ls "$(npm prefix -g 2>/dev/null)/lib/node_modules" 2>/dev/null
  true'
remote "$snapshot" > "$OUT/macos-before.txt"

work="$(remote 'mktemp -d -t rtfmup-bench')"
echo "== remote work dir: $work (rtfmup $(git -C "$ROOT" rev-parse --short HEAD))"
git -C "$ROOT" archive HEAD | remote "tar -x -C '$work'"
remote "cd '$work' && PATH=\$HOME/.local/bin:/opt/homebrew/bin:\$PATH uv sync --locked -q && touch .started"

pass=0
for repo in "${repos[@]}"; do
  echo
  echo "== [$repo]"
  status=0
  remote "cd '$work' && PATH=\$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin \
    RTFMUP_BENCH_WHERE='Mac mini as gitea-runner (non-admin, over SSH)' \
    uv run python bench/e2e.py --only '$repo' --on-host </dev/null" || status=$?
  if [ "$status" -eq 0 ]; then pass=$((pass + 1)); fi
  echo "== [$repo] exit $status"
done

# Only the reports this run wrote: the archive also carries the committed older ones.
for f in $(remote "cd '$work/bench/results' && find . -name 'e2e-*.md' -newer '$work/.started' | sed 's#^./##'"); do
  remote "cat '$work/bench/results/$f'" > "$OUT/$f"
done

remote "$snapshot" > "$OUT/macos-after.txt"
echo
echo "== left behind (after minus before):"
diff "$OUT/macos-before.txt" "$OUT/macos-after.txt" | grep '^>' || echo "   nothing"
echo "   (remote work dir $work is kept for inspection; delete it with: ssh $SSH_HOST rm -rf '$work')"

echo
if [ "$pass" -eq "${#repos[@]}" ]; then
  echo "SUCCESS: $pass of ${#repos[@]} repos came up"
else
  echo "DONE: $pass of ${#repos[@]} repos came up (reports in $OUT)"
fi
