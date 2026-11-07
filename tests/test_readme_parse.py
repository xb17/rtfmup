"""T1.7: README location and Markdown parse. Line numbers are 1-based."""

from __future__ import annotations

import pytest
from conftest import FIXTURES

from rtfmup.readme.locate import locate
from rtfmup.readme.parse import Section, parse


def titles(sec: Section) -> list[str]:
    return [s.title for s in sec.walk_sections()]


def read(name: str) -> str:
    return (FIXTURES / name / "README.md").read_text(encoding="utf-8")


def src_lines(text: str, start: int, end: int) -> list[str]:
    return text.splitlines()[start - 1 : end]


# --- locate ------------------------------------------------------------------


def test_locate_markdown():
    res = locate(FIXTURES / "py-uv")
    assert (res.readme.rel, res.readme.format) == ("README.md", "markdown")
    assert res.warnings == []


def test_locate_rst():
    res = locate(FIXTURES / "rst-readme")
    assert (res.readme.rel, res.readme.format) == ("README.rst", "rst")


def test_locate_none():
    res = locate(FIXTURES / "empty")
    assert res.readme is None and res.warnings == []


def test_locate_prefers_markdown_and_matches_case(tmp_path):
    (tmp_path / "readme.RST").write_text("x")
    (tmp_path / "ReadMe.md").write_text("# x")
    res = locate(tmp_path)
    assert res.readme.rel == "ReadMe.md"


def test_locate_root_beats_docs_and_github(tmp_path):
    for d in (".github", "docs"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "README.md").write_text("# sub")
    assert locate(tmp_path).readme.rel == ".github/README.md"
    (tmp_path / "README.md").write_text("# root")
    assert locate(tmp_path).readme.rel == "README.md"


def test_locate_unsupported_format_warns(tmp_path):
    (tmp_path / "README.txt").write_text("hi")
    (tmp_path / "README").write_text("hi")
    res = locate(tmp_path)
    assert res.readme is None
    assert set(res.warnings) == {
        "README: unsupported README format",
        "README.txt: unsupported README format",
    }


def test_locate_unsupported_does_not_hide_supported(tmp_path):
    (tmp_path / "README.txt").write_text("hi")
    (tmp_path / "README.md").write_text("# hi")
    res = locate(tmp_path)
    assert res.readme.rel == "README.md" and res.warnings == []


def test_locate_oversized_readme_ignored(tmp_path):
    (tmp_path / "README.md").write_text("x" * 2_000_001)
    res = locate(tmp_path)
    assert res.readme is None and "larger than" in res.warnings[0]


def test_locate_ignores_directory_named_readme(tmp_path):
    (tmp_path / "README.md").mkdir()
    assert locate(tmp_path).readme is None


# --- headings and line numbers -------------------------------------------------


def test_py_uv_structure_and_lines():
    text = read("py-uv")
    root = parse(text)
    assert titles(root) == ["", "py-uv fixture", "Installation", "Usage", "Notes"]
    h1 = root.children[0]
    assert (h1.level, h1.line_start, h1.line_end) == (1, 1, len(text.splitlines()))
    assert [c.title for c in h1.children] == ["Installation", "Usage", "Notes"]
    inst = h1.children[0]
    assert inst.path == ["py-uv fixture", "Installation"]
    # The section spans heading to the line before the next heading.
    assert src_lines(text, inst.line_start, inst.line_start)[0] == "## Installation"
    assert src_lines(text, inst.line_end + 1, inst.line_end + 1)[0] == "## Usage"
    (fence,) = inst.blocks[-1:]
    assert fence.kind == "fence" and fence.lang == "bash" and fence.text == "uv sync\n"
    assert src_lines(text, fence.line_start, fence.line_end) == [
        "```bash",
        "uv sync",
        "```",
    ]
    assert src_lines(text, fence.content_line_start, fence.content_line_start) == [
        "uv sync"
    ]


def test_last_section_runs_to_eof():
    text = "# A\n\ntext\n\n## B\n\nmore\n"
    root = parse(text)
    a = root.children[0]
    b = a.children[0]
    assert (a.line_start, a.line_end) == (1, 7)
    assert (b.line_start, b.line_end) == (5, 7)


def test_sibling_headings_and_level_jumps():
    root = parse("# A\n## B\n#### D\n## C\n# E\n")
    flat = [(s.title, s.level, s.path) for s in root.walk_sections()][1:]
    assert flat == [
        ("A", 1, ["A"]),
        ("B", 2, ["A", "B"]),
        ("D", 4, ["A", "B", "D"]),
        ("C", 2, ["A", "C"]),
        ("E", 1, ["E"]),
    ]
    spans = {s.title: (s.line_start, s.line_end) for s in root.walk_sections()}
    assert spans["A"] == (1, 4) and spans["B"] == (2, 3) and spans["D"] == (3, 3)
    assert spans["C"] == (4, 4) and spans["E"] == (5, 5)


def test_preamble_before_first_heading():
    root = parse("intro\n\n# A\n")
    assert [b.text for b in root.blocks] == ["intro"]
    assert root.children[0].line_start == 3


def test_setext_heading():
    root = parse("Title\n=====\n\nbody\n\nSub\n---\n")
    assert [
        (s.title, s.level, s.line_start) for s in list(root.walk_sections())[1:]
    ] == [
        ("Title", 1, 1),
        ("Sub", 2, 6),
    ]


def test_heading_inline_markup_is_flattened():
    root = parse("# Install `uv` *now*\n")
    assert root.children[0].title == "Install uv now"


def test_empty_and_whitespace_documents():
    for text in ("", "\n\n", "   \n"):
        root = parse(text)
        assert root.children == [] and root.blocks == []


def test_bom_and_crlf():
    root = parse("﻿# A\r\n\r\n```sh\r\nls\r\n```\r\n")
    (fence,) = root.children[0].blocks
    assert (fence.line_start, fence.line_end, fence.text) == (3, 5, "ls\n")


# --- blocks ------------------------------------------------------------------


def test_fence_info_strings_and_unlabeled():
    text = "```bash\na\n```\n\n```\nb\n```\n\n~~~ powershell title=x\nc\n~~~\n"
    fences = parse(text).blocks
    assert [(f.info, f.lang) for f in fences] == [
        ("bash", "bash"),
        ("", ""),
        ("powershell title=x", "powershell"),
    ]


def test_unclosed_fence_runs_to_eof():
    (f,) = parse("```sh\nls\n").blocks
    assert (f.line_start, f.line_end, f.text) == (1, 2, "ls\n")


def test_indented_code_block():
    (b,) = parse("para\n\n    npm install\n").blocks[1:]
    assert b.kind == "code_block" and b.text == "npm install\n" and b.line_start == 3


def test_paragraph_links_and_code_spans():
    (p,) = parse(
        "Get a key from [the dashboard](https://x.example/keys) and set `API_KEY`.\n"
    ).blocks
    assert p.text == "Get a key from the dashboard and set API_KEY."
    assert [(lk.text, lk.href) for lk in p.links] == [
        ("the dashboard", "https://x.example/keys")
    ]
    assert p.code_spans == ["API_KEY"]


def test_table_rows():
    text = (
        "| Name | Default |\n|---|---|\n| `PORT` | 3000 |\n| HOST | [h](http://h) |\n"
    )
    (t,) = parse(text).blocks
    assert t.kind == "table" and (t.line_start, t.line_end) == (1, 4)
    assert t.rows == [["Name", "Default"], ["PORT", "3000"], ["HOST", "h"]]
    assert t.code_spans == ["PORT"] and t.links[0].href == "http://h"


def test_blockquote_contents_are_blocks():
    (f,) = parse("> ```sh\n> make\n> ```\n").blocks
    assert f.kind == "fence" and f.text == "make\n"


def test_html_block_outside_details():
    (h,) = parse('<p align="center">logo</p>\n').blocks
    assert h.kind == "html_block"


# --- lists -------------------------------------------------------------------

LIST = """\
1. Install:

   ```bash
   npm ci
   ```

2. Run
   - dev: `npm run dev`
     ```sh
     npm run dev -- --port 3000
     ```
   - prod
"""


def test_nested_list_with_indented_fences():
    root = parse(LIST)
    items = root.blocks
    assert [(i.kind, i.depth, i.ordered, i.text) for i in items] == [
        ("list_item", 0, True, "Install:"),
        ("list_item", 0, True, "Run"),
    ]
    (fence,) = items[0].children
    assert (fence.kind, fence.lang, fence.text) == ("fence", "bash", "npm ci\n")
    assert (fence.line_start, fence.line_end) == (3, 5)

    dev, prod = items[1].children
    assert (dev.depth, dev.ordered, dev.text, dev.code_spans) == (
        1,
        False,
        "dev: npm run dev",
        ["npm run dev"],
    )
    (inner,) = dev.children
    assert inner.text == "npm run dev -- --port 3000\n"
    assert (inner.line_start, inner.line_end) == (9, 11)
    assert prod.text == "prod"
    # Every reported fence range matches the source text.
    lines = LIST.splitlines()
    for blk in (fence, inner):
        assert lines[blk.line_start - 1].lstrip().startswith("```")
        assert lines[blk.line_end - 1].lstrip().startswith("```")


def test_walk_blocks_is_in_document_order():
    root = parse(LIST)
    kinds = [(b.kind, b.line_start) for _, b in root.walk_blocks()]
    assert kinds == sorted(kinds, key=lambda t: t[1])
    assert [b.kind for _, b in root.walk_blocks()].count("fence") == 2


def test_fullstack_numbered_steps_with_fences():
    text = read("fullstack-compose")
    fences = [b for _, b in parse(text).walk_blocks() if b.kind == "fence"]
    assert [f.text for f in fences] == ["cp .env.example .env\n", "docker compose up\n"]
    lines = text.splitlines()
    for f in fences:
        assert lines[f.line_start - 1].strip().startswith("```")
        assert lines[f.content_line_start - 1].strip() == f.text.strip()
    links = [lk for _, b in parse(text).walk_blocks() for lk in b.links]
    assert [lk.href for lk in links] == ["https://dashboard.stripe.com/apikeys"]


# --- <details> ---------------------------------------------------------------

DETAILS = """\
# Project

## Install

<details>
<summary>Windows <b>users</b></summary>

```powershell
winget install Foo
```

</details>

After the details.

<details><summary>Inline</summary>

```bash
echo hi
```

</details>

## Next
"""


def test_details_pseudo_section():
    root = parse(DETAILS)
    install = root.children[0].children[0]
    assert install.title == "Install"
    win, inline = install.children
    assert (win.title, win.kind, win.path) == (
        "Windows users",
        "details",
        ["Project", "Install", "Windows users"],
    )
    assert (win.line_start, win.line_end) == (5, 12)
    assert [b.kind for b in win.blocks] == ["fence"]
    assert win.blocks[0].text == "winget install Foo\n"
    assert (inline.title, inline.kind) == ("Inline", "details")
    assert [b.text for b in inline.blocks] == ["echo hi\n"]
    # Text between the details blocks belongs to the enclosing section.
    assert [b.text for b in install.blocks] == ["After the details."]
    # A heading after the details block closes the Install section normally.
    nxt = root.children[0].children[1]
    assert nxt.title == "Next" and install.line_end == nxt.line_start - 1


def test_details_without_summary():
    root = parse("<details>\n\n```sh\nx\n```\n\n</details>\n")
    (d,) = root.children
    assert d.title == "details" and d.kind == "details"


def test_heading_inside_details_stays_inside():
    root = parse(
        "# A\n\n<details>\n<summary>S</summary>\n\n## Inner\n\ntext\n\n</details>\n\n## After\n"
    )
    a = root.children[0]
    d, after = a.children
    assert d.kind == "details" and [c.title for c in d.children] == ["Inner"]
    assert d.children[0].path == ["A", "S", "Inner"]
    assert after.title == "After"


def test_unclosed_details_runs_to_eof():
    text = "# A\n\n<details>\n<summary>S</summary>\n\ntext\n"
    d = parse(text).children[0].children[0]
    assert (d.line_start, d.line_end) == (3, 6)


def test_stray_closing_details_is_kept_as_html():
    (h,) = parse("</details>\n").blocks
    assert h.kind == "html_block"


def test_self_contained_details_block_is_plain_html():
    root = parse("<details><summary>x</summary>y</details>\n")
    assert root.children == [] and root.blocks[0].kind == "html_block"


@pytest.mark.parametrize(
    "name", [p.name for p in FIXTURES.iterdir() if (p / "README.md").exists()]
)
def test_every_fixture_readme_parses(name):
    text = read(name)
    root = parse(text)
    n = len(text.splitlines())
    for sec in root.walk_sections():
        assert 1 <= sec.line_start <= sec.line_end <= max(n, 1)
    for _, b in root.walk_blocks():
        assert 1 <= b.line_start <= b.line_end <= n
