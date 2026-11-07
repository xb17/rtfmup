"""Ruby manifests: Gemfile, Gemfile.lock, .ruby-version."""

from __future__ import annotations

import re

from rtfmup.detect._common import Detection

_RUBY = re.compile(r"""^\s*ruby\s+(['"])(?P<v>[^'"]+)\1""")


def detect(d: Detection) -> None:
    text = d.read("Gemfile")
    if text is not None:
        d.manifest("Gemfile", "gemfile", "ruby.gemfile")
        lock = "Gemfile.lock" if d.exists("Gemfile.lock") else None
        d.package_manager("bundler", lock, lock or "Gemfile", 1, "ruby.gemfile")
        for i, raw in enumerate(text.splitlines(), start=1):
            if m := _RUBY.match(raw):
                d.runtime("ruby", m.group("v"), "Gemfile", i, "ruby.gemfile_ruby")
                break
