"""T4.1: failure diagnostics. One captured-output sample per pattern."""

from __future__ import annotations

import pytest
from test_up import go, make_plan, py

from rtfmup.execute.diagnose import MAX_DIAGNOSES, MAX_LINES, PATTERNS, diagnose

SAMPLES = {
    "msvc-build-tools": [
        (
            "error: Microsoft Visual C++ 14.0 or greater is required. Get it with "
            "'Microsoft C++ Build Tools'"
        ),
        "gyp ERR! find VS could not find a version of Visual Studio 2017 or newer",
    ],
    "rust-linker": [
        "error: linker `link.exe` not found",
        "error: linking with `link.exe` failed: exit code: 1",
    ],
    "docker-daemon": [
        (
            "error during connect: Get http://%2F%2F.%2Fpipe%2Fdocker_engine/v1.24/info: "
            "open //./pipe/docker_engine: The system cannot find the file specified."
        ),
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
    ],
    "port-in-use": [
        "Error: listen EADDRINUSE: address already in use :::3000",
        "OSError: [WinError 10048] Only one usage of each socket address",
    ],
    "long-paths": [
        "error: unable to create file a/b/c: Filename too long",
        "Error: ENAMETOOLONG: name too long",
    ],
    "execution-policy": [
        "npm.ps1 cannot be loaded because running scripts is disabled on this system.",
    ],
    "wrong-runtime-version": [
        'error pkg@1.0.0: The engine "node" is incompatible with this module.',
        "ERROR: Package 'x' requires a different Python: 3.9.1 not in '>=3.11'",
        (
            "error: package `y v0.1.0` cannot be built because it requires rustc 1.88 "
            "or newer, while the currently active rustc version is 1.75"
        ),
    ],
    "file-locked-or-denied": [
        "npm error code EPERM",
        (
            "PermissionError: [WinError 32] The process cannot access the file because "
            "it is being used by another process"
        ),
        "npm ERR! code EACCES",
    ],
}


def test_every_pattern_has_samples():
    assert set(SAMPLES) == {p.id for p in PATTERNS}


@pytest.mark.parametrize(
    ("pid", "line"), [(k, v) for k, vs in SAMPLES.items() for v in vs]
)
def test_sample_is_diagnosed(pid, line):
    got = diagnose(["unrelated output", line, "more output"])
    assert pid in {d.id for d in got}, line
    d = next(d for d in got if d.id == pid)
    assert d.explanation and d.fix


def test_no_match_gives_no_diagnosis():
    assert diagnose(["Compiling foo v0.1.0", "error[E0308]: mismatched types"]) == []
    assert diagnose([]) == []


def test_only_the_last_200_lines_are_read():
    old = [SAMPLES["port-in-use"][0]] + ["noise"] * MAX_LINES
    assert diagnose(old) == []
    assert diagnose(["noise"] * MAX_LINES + [SAMPLES["port-in-use"][0]])


def test_at_most_two_most_specific_first():
    out = diagnose(
        [
            SAMPLES["file-locked-or-denied"][0],
            SAMPLES["port-in-use"][0],
            SAMPLES["msvc-build-tools"][0],
        ]
    )
    assert len(out) == MAX_DIAGNOSES
    assert [d.id for d in out] == ["msvc-build-tools", "port-in-use"]


def test_failing_step_prints_diagnosis_after_the_failure_line(tmp_path):
    code = "print('error: linker `link.exe` not found'); raise SystemExit(1)"
    plan = make_plan([py("a", "install", code)])
    result, _out, err = go(plan, tmp_path, assume_yes=True)
    assert result.exit_code == 1
    assert err.index("FAILED") < err.index("Diagnosis:") < err.index("fix:")
    assert "link.exe" in err


def test_successful_or_unmatched_failure_prints_no_diagnosis(tmp_path):
    plan = make_plan([py("a", "install", "print('boom'); raise SystemExit(1)")])
    _r, _o, err = go(plan, tmp_path, assume_yes=True)
    assert "Diagnosis:" not in err
