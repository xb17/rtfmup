#!/usr/bin/env bash
# T5.8, Linux: the e2e bench, one disposable container per repo (PLAN T5.8).
# EXECUTES third-party code, but only inside `docker run --rm`: nothing it starts or
# writes outlives the container. The host only gets the report files, in OUT.
#
#   bash bench/e2e-linux.sh                 # every e2e repo in corpus.toml
#   bash bench/e2e-linux.sh flask ripgrep   # just these
#
# The image is a plain Ubuntu LTS. Inside: git, curl and uv (needed to run rtfmup
# from this checkout), nothing else, so every runtime is rtfmup's job. The container
# runs as root, so apt steps carry no sudo, but they are still high risk, and with
# stdin closed high-risk steps are declined, as on the Windows runners.
set -euo pipefail

IMAGE="${IMAGE:-ubuntu:26.04}"  # verified 2026-11-18: Ubuntu 26.04.1 LTS
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="${OUT:-$HERE/results}"

if [ "$#" -gt 0 ]; then
  repos=("$@")
else
  mapfile -t repos < <(cd "$ROOT" && uv run python -c "
import tomllib
for r in tomllib.load(open('bench/corpus.toml', 'rb'))['repo']:
    if r.get('e2e'):
        print(r['name'])" | tr -d '\r')  # Windows Python prints CRLF
fi

# Docker Desktop on Windows needs Windows paths for bind mounts (Git Bash gives /tmp/...).
hostpath() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else echo "$1"; fi; }
mkdir -p "$OUT"

src="$(mktemp -d)"
trap 'rm -rf "$src"' EXIT
git -C "$ROOT" archive HEAD | tar -x -C "$src"
echo "== rtfmup $(git -C "$ROOT" rev-parse --short HEAD), image $IMAGE, repos: ${repos[*]}"

pass=0
for repo in "${repos[@]}"; do
  echo
  echo "== [$repo] fresh container"
  status=0
  MSYS_NO_PATHCONV=1 docker run --rm -i=false \
    -v "$(hostpath "$src"):/src:ro" -v "$(hostpath "$OUT"):/out" \
    -e RTFMUP_BENCH_WHERE="disposable $IMAGE container (docker run --rm, root)" \
    "$IMAGE" bash -euo pipefail -c '
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq >/dev/null
      apt-get install -y -qq git curl ca-certificates >/dev/null
      curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
      export PATH="$HOME/.local/bin:$PATH"
      cp -r /src /work && cd /work
      uv sync --locked -q
      set +e
      uv run python bench/e2e.py --only "'"$repo"'" --on-host </dev/null
      code=$?
      cp bench/results/e2e-*-"'"$repo"'".md /out/ 2>/dev/null
      exit $code
    ' || status=$?
  if [ "$status" -eq 0 ]; then pass=$((pass + 1)); fi
  echo "== [$repo] exit $status"
done

echo
if [ "$pass" -eq "${#repos[@]}" ]; then
  echo "SUCCESS: $pass of ${#repos[@]} repos came up"
else
  echo "DONE: $pass of ${#repos[@]} repos came up (reports in $OUT)"
fi
