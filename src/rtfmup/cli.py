"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys

from rtfmup import __version__
from rtfmup.scan import format_summary, scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtfmup",
        description="Set up any repo from its README. (Early development: only `scan` works.)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_scan = sub.add_parser("scan", help="detect manifests in a local repo")
    p_scan.add_argument("path", nargs="?", default=".", help="repo directory (default: .)")
    p_scan.add_argument("--json", action="store_true", help="emit JSON instead of a summary")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        try:
            result = scan(args.path)
        except NotADirectoryError as exc:
            print(f"rtfmup: error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(format_summary(result))
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
