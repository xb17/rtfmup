"""End-to-end benchmark runner (PLAN T3.8). EXECUTES third-party code.

    uv run python bench/e2e.py --only uptime-kuma      # on a GitHub runner (bench-e2e.yml)
    uv run python bench/e2e.py --only flask --on-host  # here, knowingly

One repo per invocation. It runs where nothing it leaves behind matters: an
ephemeral GitHub `windows-latest` runner (`.github/workflows/bench-e2e.yml`), and
refuses anywhere else unless `--on-host` is passed. A README step can start a
daemon, register a service, or write to the home directory (the pm2 run on
2026-11-14 did all three), and no amount of environment redirection stops that.

What one run does:
  1. fetches the pinned commit (same cache as run.py) and copies it into a fresh
     scratch folder named after the repo, so the cached checkout is never modified;
  2. runs `rtfmup up <copy> --yes` with the repo's answers from corpus.toml
     (`e2e_variant`, `e2e_run`), as a user answering rtfmup's questions would.
     stdin is closed: `--yes` approves low and medium risk, high-risk steps need
     a typed `yes`, and EOF is a refusal, so high-risk steps are declined;
  3. success is the PLAN §4.2 definition: the health check passes (`Up:`), or, for
     a CLI with no port, `up` finishes and the repo's `e2e_done` command succeeds;
  4. writes bench/results/e2e-<date>-<time>-<repo>.md (every attempt is kept) and
     the full output next to it in the scratch folder.

rtfmup's own dev venv is taken off PATH first: users run rtfmup from uvx or pipx,
so a repo's steps must not find rtfmup's interpreter or tools. Since M5 it also runs
on Linux (`bench/e2e-linux.sh`, one disposable container per repo) and macOS (T5.8).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fetch import bench_cache, ensure_checkout, load_corpus

HERE = Path(__file__).parent
TAIL_LINES = 80
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def step_env(scratch: Path, *, on_host: bool) -> dict[str, str]:
    """The environment rtfmup (and so every repo step) runs with."""
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV",)}
    own_bin = Path(sys.executable).parent.resolve()
    keep = []
    for entry in env.get("PATH", "").split(os.pathsep):
        try:
            if entry and Path(entry).resolve() == own_bin:
                continue
        except OSError:
            pass
        keep.append(entry)
    env["PATH"] = os.pathsep.join(keep)
    env["PYTHONUNBUFFERED"] = "1"
    if on_host:
        # Damage limitation only. It keeps `npm install -g` off the host PATH and
        # nothing else: daemons, services and home-directory state still escape.
        prefix = scratch / "npm-global"
        prefix.mkdir(parents=True, exist_ok=True)
        env["NPM_CONFIG_PREFIX"] = str(prefix)
        env["NPM_CONFIG_CACHE"] = str(scratch / "npm-cache")
        # npm puts global executables in {prefix} on Windows and {prefix}/bin
        # elsewhere (docs.npmjs.com, "folders", 2026-11-18).
        bindir = prefix if sys.platform == "win32" else prefix / "bin"
        env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    return env


def _new_group() -> dict:
    """Start rtfmup in its own process group, so stop_tree can signal all of it."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        os.killpg(proc.pid, signal.SIGINT)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], check=False)
        else:
            os.killpg(proc.pid, signal.SIGKILL)


def read_state(repo: Path) -> dict:
    try:
        return json.loads((repo / ".rtfmup" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def done_check(argv: list[str], env: dict[str, str]) -> tuple[bool, str]:
    exe = shutil.which(argv[0], path=env.get("PATH"))
    if exe is None:
        return False, f"`{argv[0]}` is not on PATH"
    proc = subprocess.run(
        [exe, *argv[1:]],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    first = (proc.stdout or proc.stderr).strip().splitlines()[:1]
    return proc.returncode == 0, f"exit {proc.returncode}: {first[0] if first else ''}"


def publish_report_path(report: Path) -> None:
    """On Actions, name this run's report as the step output `report`.

    The upload step takes exactly that file; a glob would also match the reports
    earlier runs committed to `bench/results/`.
    """
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"report={report}\n")


def plan_section(lines: list[str]) -> str:
    """The plan as rtfmup printed it, so the report shows what was attempted."""
    start = next((i for i, ln in enumerate(lines) if "rtfmup plan (" in ln), None)
    end = next((i for i, ln in enumerate(lines) if "--yes: approved" in ln), None)
    if start is None:
        return "(no plan printed)\n"
    return "".join(lines[start : (end + 1) if end is not None else start + 60])


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
    if not ON_CI and not args.on_host:
        print(
            "refusing: this executes third-party setup steps. Run it on an ephemeral\n"
            "runner (gh workflow run bench-e2e.yml), or pass --on-host to accept that\n"
            "daemons, services and home-directory writes will land on this machine.",
            file=sys.stderr,
        )
        return 2

    pinned = ensure_checkout(repo)
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    scratch = bench_cache() / "e2e" / f"{args.only}-{stamp}"
    work = scratch / args.only  # a folder named after the repo, like a real clone
    shutil.copytree(pinned, work, ignore=shutil.ignore_patterns(".rtfmup"))
    env = step_env(scratch, on_host=not ON_CI)
    print(
        f"\n== e2e: {args.only} @ {repo['sha'][:10]}\n   work dir: {work}\n", flush=True
    )

    variant = args.variant or repo.get("e2e_variant")
    run = args.run or repo.get("e2e_run")
    cmd = [sys.executable, "-m", "rtfmup.cli", "up", str(work), "--yes"]
    cmd += ["--health-timeout", str(args.health_timeout)]
    if variant:
        cmd += ["--variant", variant]
    if run:
        cmd += ["--run", run]
    print("   command:", subprocess.list2cmdline(cmd), "\n", flush=True)

    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        **_new_group(),
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

    done_cmd = repo.get("e2e_done")
    done_ok, done_note = False, "-"
    if done_cmd and proc.returncode == 0 and not timed_out:
        done_ok, done_note = done_check(done_cmd, env)
    elif done_cmd:
        done_note = "not checked: `up` did not finish cleanly"
    success = bool(up_line) or done_ok
    verdict = "SUCCESS" if success else ("TIMEOUT" if timed_out else "FAILED")
    print(f"\n== {verdict}: {args.only} in {seconds:.0f}s (exit {proc.returncode})")

    state = read_state(work)
    step_rows = "\n".join(
        f"| {sid} | {info.get('title', '')[:70]} | {info.get('status', '?')} "
        f"| {info.get('exit_code', '')} |"
        for sid, info in state.get("steps", {}).items()
    )
    where = (
        f"GitHub {os.environ.get('ImageOS', 'windows')} runner"
        if ON_CI
        # set by the T5.8 drivers: a disposable container, or the Mac mini runner user
        else os.environ.get("RTFMUP_BENCH_WHERE") or "this machine (--on-host)"
    )
    report = (
        f"# e2e: {args.only} ({stamp})\n\n"
        f"- result: **{verdict}**\n"
        f"- commit: `{repo['sha']}`\n"
        f"- where: {where}, {platform.platform()}, python {sys.version.split()[0]}\n"
        f"- duration: {seconds:.0f}s, rtfmup exit code {proc.returncode}, "
        f"outcome `{state.get('outcome', '?')}`\n"
        f"- health: {up_line[0] if up_line else 'never reached'}\n"
        f"- done check: {' '.join(done_cmd) if done_cmd else '-'} -> {done_note}\n"
        f"- answers: variant={variant or '-'} run={run or '-'}; stdin closed, so "
        f"high-risk steps are declined\n\n"
        f"## Plan\n\n```text\n{plan_section(lines)}```\n\n"
        f"## Steps (`.rtfmup/state.json`)\n\n"
        f"| step | title | status | exit |\n|---|---|---|---|\n"
        f"{step_rows or '| (none) | | | |'}\n\n"
        f"## Last {TAIL_LINES} lines of output\n\n```text\n"
        f"{''.join(lines[-TAIL_LINES:])}```\n"
    )
    out = HERE / "results" / f"e2e-{stamp}-{args.only}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report, encoding="utf-8")
    publish_report_path(out)
    (scratch / "output.log").write_text("".join(lines), encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    print(f"   report: {out}\n   full log: {scratch / 'output.log'}")
    return 0 if success else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", required=True, help="corpus name (one repo per run)")
    ap.add_argument("--variant", help="override corpus.toml e2e_variant")
    ap.add_argument("--run", help="override corpus.toml e2e_run")
    ap.add_argument(
        "--on-host",
        action="store_true",
        help="run on this machine anyway (not an ephemeral runner)",
    )
    ap.add_argument(
        "--timeout", type=int, default=1200, help="whole-run limit, seconds"
    )
    ap.add_argument("--health-timeout", type=float, default=120)
    ap.add_argument(
        "--hold", type=float, default=2, help="seconds to stay up before stopping"
    )
    return run_one(ap.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
