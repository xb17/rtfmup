"""D13: a minimal version-range comparator.

Python specs use `packaging` (PEP 440). Everything else (node `engines`, `.nvmrc`,
`go` directives, ...) goes through a small subset of npm semver: exact, partial
(`20`, `20.x`), `>= > <= < =`, `^`, `~`, `*`/`x`, whitespace AND and `||`.

`satisfies` returns True/False, or None when it cannot verify: an unparseable
spec (`lts/*`, `stable`) or an unparseable installed version. None is shown to the
user as "can't verify" and is never guessed.
"""

from __future__ import annotations

import re

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

Triple = tuple[int, int, int]

_VERSION = re.compile(r"^(?:go|v)?(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.IGNORECASE)
_TOKEN = re.compile(r"(\^|~|>=|<=|>|<|=)?\s*(v?[0-9xX*][0-9A-Za-z.*+-]*)")

# go.mod's `go 1.22`, a .NET target framework or SDK pin, and a rust-toolchain version
# all mean "this or newer will do" for the purpose of building, not an exact pin.
_BARE_IS_MINIMUM = {"go", "dotnet", "rust"}
_TFM = re.compile(r"^net(\d+(?:\.\d+)?)$", re.IGNORECASE)


def parse_version(text: str) -> Triple | None:
    """`v20.11.1` -> (20, 11, 1); missing parts are 0; `stable` -> None."""
    m = _VERSION.match(text.strip())
    if not m:
        return None
    return tuple(int(g or 0) for g in m.groups())  # type: ignore[return-value]


def _partial(text: str) -> list[int] | None:
    """`1.2` -> [1, 2]; `1.x` -> [1]; `*` -> []. None if malformed."""
    text = re.split(r"[-+]", text.lstrip("vV"), maxsplit=1)[0]
    nums: list[int] = []
    wild = False
    for part in text.split("."):
        if part in ("x", "X", "*"):
            wild = True
        elif wild or not part.isdigit():
            return None  # digits after a wildcard, or junk
        else:
            nums.append(int(part))
    if len(nums) > 3:
        return None
    return nums


def _pad(nums: list[int]) -> Triple:
    return (*nums, *([0] * (3 - len(nums))))  # type: ignore[return-value]


def _bump(nums: list[int], index: int) -> Triple:
    """Increment position `index` of the padded version and zero what follows."""
    p = list(_pad(nums))
    p[index] += 1
    for i in range(index + 1, 3):
        p[i] = 0
    return tuple(p)  # type: ignore[return-value]


# A range is (inclusive lower, exclusive upper); None = unbounded.
Range = tuple[Triple | None, Triple | None]


def _comparator(op: str, nums: list[int], *, bare_is_minimum: bool) -> Range:
    n = len(nums)
    if n == 0:
        return (None, None)  # `*`, `x`
    if op in ("", "="):
        if bare_is_minimum and op == "":
            return (_pad(nums), None)
        return (_pad(nums), _bump(nums, n - 1))
    if op == ">=":
        return (_pad(nums), None)
    if op == ">":
        return (_bump(nums, n - 1), None)
    if op == "<":
        return (None, _pad(nums))
    if op == "<=":
        return (None, _bump(nums, n - 1))
    if op == "~":
        return (_pad(nums), _bump(nums, 0 if n == 1 else 1))
    # caret: bump the first non-zero position, else the last given one
    idx = next((i for i, v in enumerate(nums) if v != 0), n - 1)
    return (_pad(nums), _bump(nums, idx))


def _alternative(text: str, *, bare_is_minimum: bool) -> list[Range] | None:
    """One `||`-free alternative: all comparators must hold. None if unparseable."""
    text = text.replace(",", " ").strip()
    if not text:
        return None
    ranges: list[Range] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            return None
        nums = _partial(m.group(2))
        if nums is None:
            return None
        op = m.group(1) or ""
        if not op and m.group(2).endswith("+"):
            op = ">="  # "Node 20+" as READMEs write it
        ranges.append(_comparator(op, nums, bare_is_minimum=bare_is_minimum))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return ranges


def _in_all(v: Triple, ranges: list[Range]) -> bool:
    return all((lo is None or v >= lo) and (hi is None or v < hi) for lo, hi in ranges)


def _target_frameworks(spec: str) -> str | None:
    """`net8.0;net9.0` -> `8.0` (the lowest modern TFM). None if there is none."""
    found = [m.group(1) for part in spec.split(";") if (m := _TFM.match(part.strip()))]
    return min(found, key=lambda v: parse_version(v) or (0, 0, 0)) if found else None


def _generic(runtime: str, v: Triple, spec: str) -> bool | None:
    spec = re.sub(r"^go(?=\d)", "", spec.strip())
    if runtime == "dotnet" and spec.lower().startswith("net"):
        tfm = _target_frameworks(spec)
        if tfm is None:
            return None  # netstandard, net48, ...
        spec = tfm
    alts = [
        _alternative(a, bare_is_minimum=runtime in _BARE_IS_MINIMUM)
        for a in spec.split("||")
    ]
    if any(a is None for a in alts):
        return None
    return any(_in_all(v, a) for a in alts if a is not None)


def satisfies(runtime: str, version: str, spec: str) -> bool | None:
    """Does the installed `version` satisfy `spec`? None = can't verify."""
    v = parse_version(version)
    if v is None or not spec.strip():
        return None
    if runtime == "python":
        try:
            return SpecifierSet(spec).contains(
                Version(".".join(map(str, v))), prereleases=True
            )
        except (InvalidSpecifier, InvalidVersion):
            pass  # e.g. a bare `3.12` from .python-version: use the generic path
    return _generic(runtime, v, spec)


_BOUND = re.compile(
    r"(?P<op>\^|~=|~|>=|<=|==|!=|>|<|=)?\s*v?(?P<ver>\d+(?:\.(?:\d+|[xX*]))*)\+?"
)


def minimum_version(runtime: str, spec: str) -> str | None:
    """The lowest version a spec allows, as written (`>=3.11,<3.13` -> `3.11`).

    Used where something needs a concrete version to install (a devcontainer Feature).
    None when the spec names no version (`lts/*`, `stable`, `nightly-2024-01-01`).
    """
    spec = spec.strip()
    if runtime == "dotnet" and spec.lower().startswith("net"):
        spec = _target_frameworks(spec) or ""
    if not spec or re.search(r"[A-Za-z]{3,}", spec):
        return None
    best: tuple[Triple, str] | None = None
    for m in _BOUND.finditer(spec):
        if m.group("op") in ("<", "<=", "!="):
            continue
        text = re.sub(r"\.[xX*].*$", "", m.group("ver"))
        triple = parse_version(text)
        if triple is not None and (best is None or triple < best[0]):
            best = (triple, text)
    return best[1] if best else None
