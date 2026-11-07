"""D13 comparator: table tests. None means "can't verify"."""

from __future__ import annotations

import pytest

from rtfmup.host.ranges import parse_version, satisfies


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("v20.11.1", (20, 11, 1)),
        ("20", (20, 0, 0)),
        ("1.99.1-nightly", (1, 99, 1)),
        ("3.12.11", (3, 12, 11)),
        ("go1.22", (1, 22, 0)),
        ("stable", None),
        ("", None),
    ],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


NODE_CASES = [
    # exact / partial
    ("20.11.1", "20.11.1", True),
    ("20.11.2", "20.11.1", False),
    ("v20.5.0", "20", True),
    ("21.0.0", "20", False),
    ("20.5.0", "20.x", True),
    ("20.5.0", "20.*", True),
    ("20.5.3", "20.5", True),
    ("20.6.0", "20.5", False),
    ("1.0.0", "*", True),
    ("1.0.0", "x", True),
    # comparators
    ("18.0.0", ">=18", True),
    ("17.9.0", ">=18", False),
    ("18.0.0", ">18", False),
    ("19.0.0", ">18", True),
    ("18.9.0", "<=18", True),
    ("19.0.0", "<=18", False),
    ("20.0.0", "<21", True),
    ("21.0.0", "<21", False),
    ("20.0.0", ">=18 <21", True),
    ("22.0.0", ">=18 <21", False),
    ("20.0.0", ">= 18", True),
    ("20.0.0", "=20.0.0", True),
    # caret
    ("1.9.0", "^1.2.3", True),
    ("1.2.2", "^1.2.3", False),
    ("2.0.0", "^1.2.3", False),
    ("0.2.9", "^0.2.3", True),
    ("0.3.0", "^0.2.3", False),
    ("0.0.3", "^0.0.3", True),
    ("0.0.4", "^0.0.3", False),
    ("18.20.0", "^18", True),
    ("19.0.0", "^18", False),
    # tilde
    ("1.2.9", "~1.2.3", True),
    ("1.3.0", "~1.2.3", False),
    ("1.9.0", "~1", True),
    ("2.0.0", "~1", False),
    # or
    ("16.0.0", "^16 || ^18", True),
    ("17.0.0", "^16 || ^18", False),
    ("18.1.0", "^16 || ^18", True),
    ("22.0.0", ">=18 || <=12", True),
    # unparseable => can't verify
    ("26.1.0", "20+", True),  # README style
    ("19.9.0", "20+", False),
    ("26.1.0", "20.5+", True),
    ("20.0.0", "lts/*", None),
    ("20.0.0", "node", None),
    ("20.0.0", "lts/iron", None),
    ("20.0.0", "^1.x.y.z", None),
    ("20.0.0", "", None),
    ("20.0.0", ">=", None),
]


@pytest.mark.parametrize(("version", "spec", "expected"), NODE_CASES)
def test_satisfies_node_style(version, spec, expected):
    assert satisfies("node", version, spec) is expected


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ("3.12.11", ">=3.11", True),
        ("3.10.9", ">=3.11", False),
        ("3.12.1", ">=3.11,<3.13", True),
        ("3.13.0", ">=3.11,<3.13", False),
        ("3.12.1", "~=3.11", True),
        ("3.12.1", "==3.12.*", True),
        ("3.12.1", "3.12", True),  # .python-version style: bare partial falls back
        ("3.12.1", "not a spec", None),
    ],
)
def test_satisfies_python_uses_pep440(version, spec, expected):
    assert satisfies("python", version, spec) is expected


@pytest.mark.parametrize(
    ("version", "spec", "expected"),
    [
        ("1.23.0", "1.22", True),  # go.mod: bare means minimum
        ("1.21.9", "1.22", False),
        ("1.22.0", "1.22.0", True),
        ("1.22.1", "1.22.0", True),
        ("1.22.1", "go1.22", True),
    ],
)
def test_go_bare_version_is_a_minimum(version, spec, expected):
    assert satisfies("go", version, spec) is expected


def test_unparseable_installed_version_cannot_verify():
    assert satisfies("node", "nightly", ">=18") is None


@pytest.mark.parametrize(
    ("runtime", "version", "spec", "expected"),
    [
        ("dotnet", "10.0.114", "net8.0", True),  # a TFM is a minimum
        ("dotnet", "7.0.400", "net8.0", False),
        ("dotnet", "9.0.320", "net8.0;net9.0", True),
        ("dotnet", "8.0.100", "net8.0-windows", None),  # suffix: cannot verify
        ("dotnet", "8.0.100", "netstandard2.0", None),
        ("dotnet", "9.0.100", "8.0.100", True),  # SDK pin: minimum
        ("dotnet", "7.0.100", "8", False),
        ("rust", "1.99.1", "1.75", True),
        ("rust", "1.70.0", "1.75", False),
        ("rust", "1.99.1", "stable", None),  # a channel is not a version
        ("rust", "1.99.1", "nightly-2024-01-01", None),
    ],
)
def test_dotnet_and_rust_specs(runtime, version, spec, expected):
    assert satisfies(runtime, version, spec) is expected
