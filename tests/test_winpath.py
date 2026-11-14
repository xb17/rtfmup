"""PATH refresh after a runtime install."""

from __future__ import annotations

import os

from rtfmup.execute.winpath import merge_paths, refreshed_path

SEP = os.pathsep


def test_merge_drops_empty_and_duplicates_keeping_order():
    merged = merge_paths(f"a{SEP}b{SEP}", f"B{SEP}c{SEP}{SEP}a")
    entries = merged.split(SEP)
    assert entries[0] == "a" and entries[1] == "b" and entries[-1] == "c"
    assert len(entries) == 3 or os.name != "nt"  # case-insensitive on Windows


def test_refreshed_path_puts_registry_first_and_keeps_extras():
    reg = {"machine": f"M1{SEP}M2", "user": "U1"}
    out = refreshed_path(f"EXTRA{SEP}M1", read=lambda hive, _k: reg[hive])
    assert out.split(SEP) == ["M1", "M2", "U1", "EXTRA"]
