"""reStructuredText README -> the same Section tree as parse.py (D18).

docutils is run with file insertion and raw output disabled and with config
files ignored, so a README can't read local files or inject output. Unknown
directives and roles become ignored system messages, never exceptions.

docutils line numbers are approximate. Fence ranges are recovered exactly by
locating the literal block's text in the source; anything that can't be placed
that way is flagged `approximate`.
"""

from __future__ import annotations

import io
import re

from docutils import nodes
from docutils.core import publish_doctree

from rtfmup.readme.parse import Block, Link, Section

# Verified against docutils 0.23: file_insertion_enabled and raw_enabled are
# parser settings; report_level/halt_level/warning_stream/_disable_config are
# general settings. report_level 5 silences reports, halt_level 5 never halts.
SETTINGS = {
    "file_insertion_enabled": False,
    "raw_enabled": False,
    "report_level": 5,
    "halt_level": 5,
    "warning_stream": None,  # replaced per call with a StringIO
    "_disable_config": True,
    "input_encoding": "unicode",
    "syntax_highlight": "none",  # no pygments lexing; the language class is kept
    "doctitle_xform": False,
    "sectsubtitle_xform": False,
    "smart_quotes": False,
}
_ADORNMENT = re.compile(r"^([!-/:-@\[-`{-~])\1{2,}\s*$")


class _Locator:
    """Finds source lines for docutils nodes by searching forward from a cursor."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.cursor = 0  # 0-based index of the next line to consider

    def title(self, text: str) -> int | None:
        """1-based line of a section title (a text line followed by an adornment)."""
        want = " ".join(text.split())
        for i in range(self.cursor, len(self.lines) - 1):
            if " ".join(self.lines[i].split()) == want and _ADORNMENT.match(
                self.lines[i + 1]
            ):
                self.cursor = i + 1
                return i + 1
        return None

    def literal(self, text: str) -> tuple[int, int] | None:
        """1-based (first, last) content lines of a literal block, or None."""
        body = [ln.strip() for ln in text.splitlines()]
        n = len(body)
        if n == 0:
            return None
        for i in range(self.cursor, len(self.lines) - n + 1):
            if all(self.lines[i + k].strip() == body[k] for k in range(n)):
                self.cursor = i + n
                return (i + 1, i + n)
        return None

    def directive_line(self, content_first: int) -> int:
        """1-based line of the `..` directive or `::` paragraph above a literal block."""
        i = content_first - 2  # 0-based index of the line above the content
        while i > 0 and (
            not self.lines[i].strip() or self.lines[i].lstrip().startswith(":")
        ):
            i -= 1
        return max(i, 0) + 1


def _inline(node: nodes.Node) -> tuple[list[str], list[Link]]:
    spans = [n.astext() for n in node.findall(nodes.literal)]
    links = [
        Link(n.astext().strip(), str(n["refuri"]))
        for n in node.findall(nodes.reference)
        if n.get("refuri")
    ]
    return spans, links


def _is_directive_fallback(node: nodes.literal_block, parent: nodes.Element) -> bool:
    """Disabled/unknown directives leave `[system_message, literal_block(".. x::")]`."""
    idx = parent.index(node)
    prev = parent[idx - 1] if idx > 0 else None
    return isinstance(prev, nodes.system_message) and node.astext().lstrip().startswith(
        ".."
    )


class _Builder:
    def __init__(self, source: str, total: int) -> None:
        self.loc = _Locator(source.splitlines())
        self.total = total

    # --- blocks ---------------------------------------------------------------
    def fence(self, node: nodes.literal_block) -> Block:
        classes = [c for c in node.get("classes", []) if c != "code"]
        coded = "code" in node.get("classes", [])
        info = classes[0] if coded and classes else ""
        text = node.astext() + "\n"
        span = self.loc.literal(node.astext())
        if span:
            first, last = span
            start = self.loc.directive_line(first)
            return Block(
                "fence",
                start,
                last,
                text=text,
                info=info,
                content_line=first,
                low_confidence=not coded,
            )
        line = node.line or 1
        return Block(
            "fence",
            line,
            line,
            text=text,
            info=info,
            approximate=True,
            low_confidence=not coded,
        )

    def block(self, node: nodes.Node, parent: nodes.Element, depth: int) -> list[Block]:
        if isinstance(node, nodes.system_message | nodes.comment | nodes.target):
            return []
        if isinstance(node, nodes.literal_block):
            if _is_directive_fallback(node, parent):
                return []
            return [self.fence(node)]
        if isinstance(node, nodes.paragraph):
            spans, links = _inline(node)
            line = node.line or 1
            return [
                Block(
                    "paragraph",
                    line,
                    line,
                    text=node.astext(),
                    code_spans=spans,
                    links=links,
                    approximate=True,
                )
            ]
        if isinstance(node, nodes.bullet_list | nodes.enumerated_list):
            ordered = isinstance(node, nodes.enumerated_list)
            return [
                self.item(li, ordered, depth)
                for li in node.children
                if isinstance(li, nodes.list_item)
            ]
        if isinstance(node, nodes.table):
            rows = [
                [c.astext() for c in row.children] for row in node.findall(nodes.row)
            ]
            spans, links = _inline(node)
            line = node.line or 1
            return [
                Block(
                    "table",
                    line,
                    line,
                    rows=rows,
                    code_spans=spans,
                    links=links,
                    approximate=True,
                )
            ]
        if isinstance(
            node, nodes.block_quote | nodes.definition_list | nodes.container
        ):
            out: list[Block] = []
            for child in node.children:
                if isinstance(child, nodes.definition_list_item):
                    for sub in child.children:
                        if isinstance(sub, nodes.definition):
                            out += self.children(sub, depth)
                else:
                    out += self.block(child, node, depth)
            return out
        return []

    def children(self, parent: nodes.Element, depth: int) -> list[Block]:
        out: list[Block] = []
        for child in parent.children:
            out += self.block(child, parent, depth)
        return out

    def item(self, li: nodes.list_item, ordered: bool, depth: int) -> Block:
        kids = self.children(li, depth + 1)
        line = li.line or 1
        item = Block(
            "list_item",
            line,
            line,
            depth=depth,
            ordered=ordered,
            children=kids,
            approximate=True,
        )
        if kids and kids[0].kind == "paragraph":
            first = kids.pop(0)
            item.text, item.code_spans, item.links = (
                first.text,
                first.code_spans,
                first.links,
            )
            item.line_start = item.line_end = first.line_start
        return item

    # --- sections -------------------------------------------------------------
    def section(self, node: nodes.section, parent: Section, level: int) -> Section:
        title_node = next(
            (c for c in node.children if isinstance(c, nodes.title)), None
        )
        title = title_node.astext().strip() if title_node is not None else ""
        found = self.loc.title(title)
        approx = found is None
        start = found if found else max((node.line or 2) - 1, 1)
        sec = Section(
            title,
            level,
            start,
            self.total,
            path=[*parent.path, title],
            approximate=approx,
        )
        body = [c for c in node.children if c is not title_node]
        for child in body:
            if isinstance(child, nodes.section):
                sec.children.append(self.section(child, sec, level + 1))
            else:
                sec.blocks += self.block(child, node, 0)
        return sec


def _finish_ends(sec: Section, end: int) -> None:
    """Each section ends the line before its next sibling; the last runs to `end`."""
    sec.line_end = max(end, sec.line_start)
    for i, child in enumerate(sec.children):
        nxt = (
            sec.children[i + 1].line_start - 1
            if i + 1 < len(sec.children)
            else sec.line_end
        )
        _finish_ends(child, nxt)


def parse_rst(source: str) -> Section:
    """Parse rST text into a root Section (level 0), mirroring `parse.parse`."""
    source = source.removeprefix("﻿")
    total = max(1, len(source.splitlines()))
    root = Section("", 0, 1, total, kind="root", path=[])
    sink = io.StringIO()
    try:
        doc = publish_doctree(
            source, settings_overrides={**SETTINGS, "warning_stream": sink}
        )
    except Exception as exc:  # noqa: BLE001 - untrusted input must never crash a scan
        root.warnings.append(f"rst: cannot parse ({type(exc).__name__}: {exc})")
        return root

    builder = _Builder(source, total)
    for child in doc.children:
        if isinstance(child, nodes.section):
            root.children.append(builder.section(child, root, 1))
        else:
            root.blocks += builder.block(child, doc, 0)
    _finish_ends(root, total)
    return root
