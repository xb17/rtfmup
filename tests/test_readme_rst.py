"""T1.7b: rST README parse (docutils, safe configuration)."""

from __future__ import annotations

from conftest import FIXTURES

from rtfmup.readme.parse import Section, parse
from rtfmup.readme.rst import parse_rst


def shape(sec: Section) -> tuple:
    """Titles, block kinds and fence languages: what extract/classify will see."""
    blocks = [
        (b.kind, b.lang if b.kind == "fence" else "") for _, b in sec.walk_blocks()
    ]
    return (
        sec.title,
        sec.level,
        [shape(c) for c in sec.children],
        blocks if not sec.children else None,
    )


def tree(sec: Section) -> list[tuple]:
    return [
        (s.title, s.level, [(b.kind, b.text.strip()) for b in s.blocks])
        for s in sec.walk_sections()
    ]


def fences(sec: Section):
    return [b for _, b in sec.walk_blocks() if b.kind == "fence"]


def test_rst_twin_matches_markdown_section_shape():
    md = parse((FIXTURES / "py-uv" / "README.md").read_text(encoding="utf-8"))
    rst = parse_rst(
        (FIXTURES / "rst-readme" / "README.rst").read_text(encoding="utf-8")
    )
    assert [(t, lv) for t, lv, _ in tree(rst)] == [(t, lv) for t, lv, _ in tree(md)]
    # Same blocks, in the same sections, with the same text. The only difference
    # allowed is the fence language of the bare `::` block.
    for (_, _, rb), (_, _, mb) in zip(tree(rst), tree(md), strict=True):
        assert rb == mb


def test_rst_fences_languages_and_confidence():
    root = parse_rst(
        (FIXTURES / "rst-readme" / "README.rst").read_text(encoding="utf-8")
    )
    got = [(f.lang, f.text, f.low_confidence) for f in fences(root)]
    assert got == [
        ("bash", "uv sync\n", False),
        ("bash", "uv run py-uv-fixture\n", False),
        ("", "uv run python -V\n", True),  # bare `::` block: language unknown
    ]


def test_rst_fence_line_numbers_point_at_source():
    text = (FIXTURES / "rst-readme" / "README.rst").read_text(encoding="utf-8")
    lines = text.splitlines()
    for f in fences(parse_rst(text)):
        assert not f.approximate
        assert lines[f.content_line_start - 1].strip() == f.text.splitlines()[0]
        assert lines[f.line_end - 1].strip() == f.text.splitlines()[-1]
        first = lines[f.line_start - 1].strip()
        assert first.startswith("..") or first.endswith("::")


def test_rst_section_lines_and_nesting():
    text = (FIXTURES / "rst-readme" / "README.rst").read_text(encoding="utf-8")
    lines = text.splitlines()
    root = parse_rst(text)
    for sec in list(root.walk_sections())[1:]:
        assert not sec.approximate
        assert lines[sec.line_start - 1].strip() == sec.title
    top = root.children[0]
    assert [c.title for c in top.children] == ["Installation", "Usage", "Notes"]
    assert top.children[0].path == ["py-uv fixture", "Installation"]
    # Each section ends the line before the next one starts; the last runs to EOF.
    assert top.children[0].line_end == top.children[1].line_start - 1
    assert top.children[-1].line_end == len(lines)


def test_include_directive_is_not_followed(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET-CONTENT\n")
    doc = f"Title\n=====\n\n.. include:: {secret.as_posix()}\n\nafter\n"
    root = parse_rst(doc)
    everything = " ".join(b.text for _, b in root.walk_blocks())
    assert "TOPSECRET" not in everything
    assert fences(root) == []  # the directive's error fallback is not a code fence
    assert [b.text for _, b in root.walk_blocks()] == ["after"]


def test_raw_directive_is_disabled():
    root = parse_rst("T\n=\n\n.. raw:: html\n\n   <script>alert(1)</script>\n\ntext\n")
    assert [b.text for _, b in root.walk_blocks()] == ["text"]


def test_unknown_directive_and_role_do_not_crash():
    doc = "T\n=\n\n.. frobnicate:: x\n   :opt: y\n\nSee :weird:`thing` here.\n\n.. code-block:: sh\n\n   ls\n"
    root = parse_rst(doc)
    assert root.warnings == []
    assert [f.text for f in fences(root)] == ["ls\n"]


def test_code_directives_and_aliases():
    doc = (
        "T\n=\n\n.. code:: python\n\n   print(1)\n\n"
        ".. sourcecode:: bash\n\n   echo hi\n\n"
        ".. code-block::\n\n   no language\n"
    )
    got = [(f.lang, f.text, f.low_confidence) for f in fences(parse_rst(doc))]
    assert got == [
        ("python", "print(1)\n", False),
        ("bash", "echo hi\n", False),
        ("", "no language\n", False),
    ]


def test_options_between_directive_and_content(tmp_path):
    doc = "T\n=\n\n.. code-block:: bash\n   :class: x\n   :name: y\n\n   make\n"
    (f,) = fences(parse_rst(doc))
    lines = doc.splitlines()
    assert lines[f.line_start - 1] == ".. code-block:: bash"
    assert lines[f.content_line_start - 1].strip() == "make"


def test_repeated_identical_blocks_get_distinct_lines():
    doc = "T\n=\n\n::\n\n   make\n\nMore::\n\n   make\n"
    a, b = fences(parse_rst(doc))
    assert a.content_line_start < b.content_line_start
    assert (a.content_line_start, b.content_line_start) == (6, 10)


def test_nested_lists_with_literal_blocks():
    doc = (
        "T\n=\n\n"
        "1. Install::\n\n"
        "      npm ci\n\n"
        "2. Run\n\n"
        "   - dev: ``npm run dev``\n"
        "   - prod\n"
    )
    root = parse_rst(doc)
    top = root.children[0].blocks
    assert [(i.kind, i.depth, i.ordered, i.text) for i in top] == [
        ("list_item", 0, True, "Install:"),
        ("list_item", 0, True, "Run"),
    ]
    assert [f.text for f in top[0].children] == ["npm ci\n"]
    dev, prod = top[1].children
    assert (dev.kind, dev.depth, dev.ordered, dev.code_spans) == (
        "list_item",
        1,
        False,
        ["npm run dev"],
    )
    assert prod.text == "prod"


def test_links_and_code_spans():
    root = parse_rst(
        "T\n=\n\nGet a key from `the dashboard <https://x.example/keys>`_ and set ``API_KEY``.\n"
    )
    (p,) = root.children[0].blocks
    assert [(lk.text, lk.href) for lk in p.links] == [
        ("the dashboard", "https://x.example/keys")
    ]
    assert p.code_spans == ["API_KEY"]


def test_simple_and_grid_tables():
    doc = (
        "T\n=\n\n"
        "=====  =======\nName   Default\n=====  =======\nPORT   3000\n=====  =======\n\n"
        "+------+-----+\n| Host | Val |\n+------+-----+\n| a    | b   |\n+------+-----+\n"
    )
    simple, grid = [b for _, b in parse_rst(doc).walk_blocks() if b.kind == "table"]
    assert simple.rows == [["Name", "Default"], ["PORT", "3000"]]
    assert grid.rows == [["Host", "Val"], ["a", "b"]]
    assert simple.approximate and grid.approximate


def test_preamble_and_overlined_titles():
    doc = "intro text\n\n=======\nProject\n=======\n\nbody\n"
    root = parse_rst(doc)
    assert [b.text for b in root.blocks] == ["intro text"]
    assert root.children[0].title == "Project"


def test_empty_and_garbage_documents():
    for text in ("", "\n\n", "   \n", "\x00\x01 weird", "=====\n=====\n", "::\n"):
        root = parse_rst(text)  # must not raise
        assert isinstance(root, Section)


def test_bom_and_crlf():
    root = parse_rst("﻿T\r\n=\r\n\r\n::\r\n\r\n   ls\r\n")
    (f,) = fences(root)
    assert f.text == "ls\n" and f.content_line_start == 6


def test_docutils_config_in_cwd_is_ignored(tmp_path, monkeypatch):
    # A hostile docutils.conf must not re-enable raw or file insertion.
    (tmp_path / "docutils.conf").write_text(
        "[general]\nfile_insertion_enabled: yes\nraw_enabled: yes\n"
    )
    (tmp_path / "x.txt").write_text("INSERTED")
    monkeypatch.chdir(tmp_path)
    root = parse_rst("T\n=\n\n.. include:: x.txt\n\n.. raw:: html\n\n   <b>RAW</b>\n")
    assert "INSERTED" not in " ".join(b.text for _, b in root.walk_blocks())
    assert "RAW" not in " ".join(b.text for _, b in root.walk_blocks())


def test_csv_table_file_option_is_disabled(tmp_path):
    (tmp_path / "d.csv").write_text("secret,1\n")
    doc = f"T\n=\n\n.. csv-table::\n   :file: {(tmp_path / 'd.csv').as_posix()}\n"
    root = parse_rst(doc)
    assert [b for _, b in root.walk_blocks() if b.kind == "table"] == []


def test_directive_with_unsupported_sphinx_option_is_dropped_not_fatal():
    # docutils rejects Sphinx-only options like :linenos:. GitHub's renderer
    # (docutils) drops the block too, so it is not a command we should trust.
    doc = "T\n=\n\n.. code-block:: bash\n   :linenos:\n\n   make\n\nafter\n"
    root = parse_rst(doc)
    assert fences(root) == []
    assert root.warnings == []
