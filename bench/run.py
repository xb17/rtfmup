"""Real-repo benchmark runner (PLAN §4.2). Read-only: nothing from the corpus is executed.

    uv run python bench/run.py                 # fetch pinned repos, analyze, print + write results
    uv run python bench/run.py --only flask    # a subset
    uv run python bench/run.py --no-held-out   # skip the frozen held-out repos

Results go to bench/results/<date>.md. The held-out repos are reported separately
and are only to be looked at at milestone ends.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))  # allow `python bench/run.py`

from fetch import ensure_checkout, load_corpus
from metrics import (
    Aggregate,
    Gold,
    RepoScore,
    aggregate,
    pct,
    score_repo,
)

from rtfmup.analyze import build_analysis

HERE = Path(__file__).parent


def load_gold(directory: Path, name: str) -> Gold:
    path = directory / f"{name}.yaml"
    with path.open(encoding="utf-8") as fh:
        return Gold.model_validate(yaml.safe_load(fh))


def run_bench(
    corpus: list[dict],
    gold_dir: Path,
    checkout: Callable[[dict], Path] = ensure_checkout,
) -> list[RepoScore]:
    scores: list[RepoScore] = []
    for repo in corpus:
        gold = load_gold(gold_dir, repo["name"])
        root = checkout(repo)
        start = time.perf_counter()
        analysis = build_analysis(root)  # no cache: never write into the clone
        seconds = time.perf_counter() - start
        scores.append(
            score_repo(
                analysis,
                gold,
                ecosystem=repo["ecosystem"],
                held_out=bool(repo.get("held_out", False)),
                seconds=seconds,
            )
        )
    return scores


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    return [_row(header), _row(["---"] * len(header)), *[_row(r) for r in rows]]


def _agg_row(a: Aggregate) -> list[str]:
    return [
        a.label,
        str(a.repos),
        pct(a.recall),
        pct(a.precision),
        pct(a.category_accuracy),
        pct(a.env_recall),
        f"{a.seconds:.2f}",
    ]


AGG_HEADER = [
    "set",
    "repos",
    "step recall",
    "precision",
    "category acc.",
    "env recall",
    "seconds",
]


def render(scores: list[RepoScore]) -> str:
    dev = [s for s in scores if not s.held_out]
    held = [s for s in scores if s.held_out]
    lines = [f"# rtfmup benchmark, {date.today().isoformat()}", ""]

    lines += ["## Development set (per repo)", ""]
    lines += _table(
        ["repo", "ecosystem", "gold", "recall", "extracted", "precision", "cat. acc.", "env", "risk flags", "conflicts", "questions", "sec"],
        [
            [
                s.name, s.ecosystem, str(s.gold_steps), pct(s.recall), str(s.extracted),
                pct(s.precision), pct(s.category_accuracy), pct(s.env_recall),
                str(s.risk_flags), str(s.conflicts), str(s.ambiguities), f"{s.seconds:.2f}",
            ]
            for s in dev
        ],
    )  # fmt: skip

    lines += ["", "## Aggregate (micro-averaged)", ""]
    rows = [_agg_row(aggregate("development", dev))]
    rows.append(_agg_row(aggregate("all", scores)))
    lines += _table(AGG_HEADER, rows)

    by_eco: dict[str, list[RepoScore]] = defaultdict(list)
    for s in dev:
        by_eco[s.ecosystem].append(s)
    lines += ["", "## Per ecosystem (development set)", ""]
    lines += _table(
        AGG_HEADER,
        [_agg_row(aggregate(eco, group)) for eco, group in sorted(by_eco.items())],
    )

    if held:
        lines += ["", "## Held-out set (frozen: look at milestone ends only)", ""]
        lines += _table(
            [
                "repo",
                "ecosystem",
                "gold",
                "recall",
                "extracted",
                "precision",
                "cat. acc.",
                "env",
            ],
            [
                [
                    s.name,
                    s.ecosystem,
                    str(s.gold_steps),
                    pct(s.recall),
                    str(s.extracted),
                    pct(s.precision),
                    pct(s.category_accuracy),
                    pct(s.env_recall),
                ]
                for s in held
            ],
        )
        lines += ["", *_table(AGG_HEADER, [_agg_row(aggregate("held-out", held))])]

    lines += ["", "## Misses and extras (development set)", ""]
    for s in dev:
        if s.missed or s.extra:
            lines += [f"### {s.name}", ""]
            lines += [f"- missed {m}" for m in s.missed[:25]]
            lines += [f"- extra {e}" for e in s.extra[:25]]
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--only", nargs="*", default=[], help="repo names to run")
    ap.add_argument(
        "--no-held-out", action="store_true", help="skip the held-out repos"
    )
    ap.add_argument("--corpus", type=Path, default=HERE / "corpus.toml")
    ap.add_argument("--gold", type=Path, default=HERE / "gold")
    ap.add_argument("--out", type=Path, default=HERE / "results")
    args = ap.parse_args(argv)

    corpus = [
        r
        for r in load_corpus(args.corpus)
        if (not args.only or r["name"] in args.only)
        and not (args.no_held_out and r.get("held_out"))
    ]
    scores = run_bench(corpus, args.gold)
    text = render(scores)
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / f"{date.today().isoformat()}.md"
    out.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.buffer.write(text.encode("utf-8"))
    print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
