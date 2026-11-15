"""End-to-end benchmark runner (PLAN T3.8). EXECUTES third-party code.

    uv run python bench/e2e.py --only uptime-kuma --variant native --run "node server/server.js"

One repo per invocation. Run it on purpose, never from CI.

What it does:
  1. fetches the pinned commit (same cache as run.py) and copies it to a fresh
     scratch directory, so the cached checkout is never modified;
  2. runs `rtfmup up <copy> --yes` with stdin closed. `--yes` approves low and
     medium risk steps; a high-risk step needs a typed `yes`, and EOF counts as
     a refusal, so those are declined by construction;
  3. points npm's global prefix and cache at the scratch directory, so a README
     that says `npm install -g x` installs into the sandbox, not onto the host;
  4. as soon as `Up:` appears, sends Ctrl+C-equivalent (CTRL_BREAK on Windows)
     and records success. Otherwise it records the failure after --timeout.

It does NOT sandbox everything: pip/uv/cargo/winget/scoop installs, writes
outside the repo, and anything else a README step does are still real. That is
why each run is a stop point. Windows only until M5.

Results go to bench/results/e2e-<date>-<repo>.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fetch import bench_cache, ensure_checkout, load_corpus

HERE = Path(__file__).parent
TAIL_LINES = 60


def sandbox_env(scratch: Path) -> dict[str, str]:
    """Environment that keeps npm's global installs and cache inside `scratch`."""
    prefix = scratch / "npm-global"
    prefix.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["NPM_CONFIG_PREFIX"] = str(prefix)
    env["NPM_CONFIG_CACHE"] = str(scratch / "npm-cache")
    env["PATH"] = str(prefix) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    # A bare `pip install x` from a README must not land in the host's Python.
    env["PIP_REQUIRE_VIRTUALENV"] = "1"
    return env


def stop_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False)
        else:
            proc.kill()


def read_state(repo: Path) -> dict:
    path = repo / ".rtfmup" / "state.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_one(args: argparse.Namespace) -> int:
    # The child's output is arbitrary text; never let a console codepage kill the pump.
    sys.stdout.reconfigure(errors="replace")
    repos = {r["name"]: r for r in load_corpus()}
    repo = repos.get(args.only)
    if repo is None:
        print(
            f"unknown repo {args.only!r}; choose from {sorted(repos)}", file=sys.stderr
        )
        return 2
    if not repo.get("e2e"):
        print(f"{args.only} is not marked e2e in corpus.toml", file=sys.stderr)
        return 2
    if sys.platform != "win32":
        print("e2e bench runs on Windows only until M5", file=sys.stderr)
        return 2

    pinned = ensure_checkout(repo)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    scratch = bench_cache() / "e2e" / f"{args.only}-{stamp}"
    work = scratch / args.only  # a folder named after the repo, like a real clone
    shutil.copytree(pinned, work, ignore=shutil.ignore_patterns(".rtfmup"))
    print(
        f"\n== e2e: {args.only} @ {repo['sha'][:10]}\n   work dir: {work}\n", flush=True
    )

    cmd = [sys.executable, "-m", "rtfmup.cli", "up", str(work), "--yes"]
    cmd += ["--health-timeout", str(args.health_timeout)]
    if args.variant:
        cmd += ["--variant", args.variant]
    if args.run:
        cmd += ["--run", args.run]
    if args.component:
        cmd += ["--component", args.component]
    print("   command:", " ".join(cmd), "\n", flush=True)

    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=sandbox_env(scratch),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    lines: list[str] = []
    up_line: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)  # live progress
            lines.append(line)
            if line.lstrip().startswith("Up:") and not up_line:
                up_line.append(line.strip())
                threading.Timer(args.hold, stop_tree, args=(proc,)).start()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        print(f"\n!! timeout after {args.timeout}s; stopping", flush=True)
        stop_tree(proc)
    except KeyboardInterrupt:
        print("\n!! interrupted; stopping", flush=True)
        stop_tree(proc)
    reader.join(timeout=10)
    seconds = time.monotonic() - started

    state = read_state(work)
    steps = state.get("steps", {})
    success = bool(up_line)
    verdict = "SUCCESS" if success else ("TIMEOUT" if timed_out else "FAILED")
    print(f"\n== {verdict}: {args.only} in {seconds:.0f}s (exit {proc.returncode})")

    out = HERE / "results" / f"e2e-{date.today().isoformat()}-{args.only}.md"
    out.parent.mkdir(exist_ok=True)
    tail = "".join(lines[-TAIL_LINES:])
    step_rows = "\n".join(
        f"| {sid} | {info.get('status', '?')} | {info.get('exit_code', '')} |"
        for sid, info in steps.items()
    )
    out.write_text(
        f"# e2e: {args.only}\n\n"
        f"- result: **{verdict}**\n"
        f"- commit: `{repo['sha']}`\n"
        f"- host: windows, python {sys.version.split()[0]}\n"
        f"- duration: {seconds:.0f}s, rtfmup exit code {proc.returncode}\n"
        f"- up line: {up_line[0] if up_line else 'never reached'}\n"
        f"- answers given: variant={args.variant or '-'} run={args.run or '-'} "
        f"component={args.component or '-'}\n"
        f"- sandbox: npm global prefix and cache inside the scratch dir; stdin closed "
        f"(high-risk steps declined)\n\n"
        f"## Steps (`.rtfmup/state.json`)\n\n"
        f"| step | status | exit |\n|---|---|---|\n{step_rows or '| (none) | | |'}\n\n"
        f"## Last {TAIL_LINES} lines of output\n\n```text\n{tail}```\n",
        encoding="utf-8",
    )
    print(f"   report: {out}\n   scratch: {scratch} (delete when done)")
    return 0 if success else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", required=True, help="corpus name (one repo per run)")
    ap.add_argument("--variant")
    ap.add_argument("--run", help="answer for rtfmup's --run question")
    ap.add_argument("--component")
    ap.add_argument("--timeout", type=int, default=900, help="whole-run limit, seconds")
    ap.add_argument("--health-timeout", type=float, default=120)
    ap.add_argument(
        "--hold", type=float, default=2, help="seconds to stay up before stopping"
    )
    return run_one(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
