"""Markdown README -> Section tree with 1-based line numbers (D1: markdown-it-py).

Sections follow headings. An HTML `<details><summary>X</summary>` ... `</details>`
pair becomes a pseudo-section titled X. Blocks keep their source line range so a
command can later be reported as `README.md:12`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

from markdown_it import MarkdownIt
from markdown_it.token import Token

BlockKind = Literal[
    "paragraph", "fence", "code_block", "list_item", "table", "html_block"
]
SectionKind = Literal["root", "heading", "details"]


@dataclass
class Link:
    text: str
    href: str


@dataclass
class Block:
    kind: BlockKind
    line_start: int  # 1-based, inclusive
    line_end: int  # 1-based, inclusive
    text: str = ""  # paragraph/list item text; fence/code/html content
    info: str = ""  # fence info string (e.g. "bash", "console")
    rows: list[list[str]] = field(default_factory=list)  # tables (header row first)
    code_spans: list[str] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)  # list item contents
    depth: int = 0  # list nesting depth
    ordered: bool = False  # list items only
    # First line of a fence's content when it is not simply line_start + 1 (rST).
    content_line: int | None = None
    approximate: bool = False  # line numbers are a best guess (rST paragraphs, tables)
    low_confidence: bool = False  # rST bare `::` literal block: language unknown

    @property
    def lang(self) -> str:
        """First word of the fence info string, lower-cased."""
        return self.info.split(None, 1)[0].lower() if self.info.split() else ""

    @property
    def content_line_start(self) -> int:
        """First line of a fence's content (the line after the opening marker)."""
        if self.content_line is not None:
            return self.content_line
        return self.line_start + 1 if self.kind == "fence" else self.line_start

    def walk(self) -> Iterator[Block]:
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass
class Section:
    title: str
    level: int  # 0 = preamble/root, 1-6 = heading level, details = parent level + 1
    line_start: int
    line_end: int
    kind: SectionKind = "heading"
    path: list[str] = field(default_factory=list)  # heading trail incl. this title
    blocks: list[Block] = field(default_factory=list)
    children: list[Section] = field(default_factory=list)
    approximate: bool = False  # heading line is a best guess (rST)
    warnings: list[str] = field(default_factory=list)  # set on the root only

    def walk_sections(self) -> Iterator[Section]:
        yield self
        for child in self.children:
            yield from child.walk_sections()

    def walk_blocks(self) -> Iterator[tuple[Section, Block]]:
        """Every block under this section, in document order, with its section."""
        # A section's own blocks and its subsections interleave in the source, so
        # order by line number.
        items: list[tuple[int, Section, Block]] = []
        for sec in self.walk_sections():
            for blk in sec.blocks:
                for b in blk.walk():
                    items.append((b.line_start, sec, b))
        items.sort(key=lambda t: t[0])
        for _, sec, blk in items:
            yield sec, blk


_MD = MarkdownIt("commonmark").enable("table")
_DETAILS_OPEN = re.compile(r"<details\b", re.IGNORECASE)
_DETAILS_CLOSE = re.compile(r"</details\s*>", re.IGNORECASE)
_SUMMARY = re.compile(r"<summary[^>]*>(.*?)</summary\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _lines(tok: Token) -> tuple[int, int]:
    if tok.map is None:  # not expected for block tokens we use
        return (1, 1)
    return (tok.map[0] + 1, tok.map[1])


def _inline(tok: Token) -> tuple[str, list[str], list[Link]]:
    text: list[str] = []
    spans: list[str] = []
    links: list[Link] = []
    link_href: str | None = None
    link_text: list[str] = []

    def emit(s: str) -> None:
        text.append(s)
        if link_href is not None:
            link_text.append(s)

    for child in tok.children or []:
        t = child.type
        if t == "text":
            emit(child.content)
        elif t == "code_inline":
            spans.append(child.content)
            emit(child.content)
        elif t in ("softbreak", "hardbreak"):
            emit("\n")
        elif t == "image":
            emit(child.content)
        elif t == "link_open":
            link_href = str(child.attrGet("href") or "")
            link_text = []
        elif t == "link_close":
            if link_href is not None:
                links.append(Link("".join(link_text).strip(), link_href))
            link_href = None
    return "".join(text), spans, links


def _paragraphish(toks: list[Token], i: int) -> tuple[Block, int]:
    """paragraph_open/heading_open, inline, *_close"""
    start, end = _lines(toks[i])
    text, spans, links = _inline(toks[i + 1])
    return Block(
        "paragraph", start, end, text=text, code_spans=spans, links=links
    ), i + 3


def _table(toks: list[Token], i: int) -> tuple[Block, int]:
    start, end = _lines(toks[i])
    rows: list[list[str]] = []
    spans: list[str] = []
    links: list[Link] = []
    i += 1
    while toks[i].type != "table_close":
        t = toks[i]
        if t.type == "tr_open":
            rows.append([])
        elif t.type == "inline":
            text, s, lk = _inline(t)
            rows[-1].append(text)
            spans += s
            links += lk
        i += 1
    return Block("table", start, end, rows=rows, code_spans=spans, links=links), i + 1


def _list(toks: list[Token], i: int, depth: int) -> tuple[list[Block], int]:
    ordered = toks[i].type == "ordered_list_open"
    close = "ordered_list_close" if ordered else "bullet_list_close"
    items: list[Block] = []
    i += 1
    while toks[i].type != close:
        if toks[i].type != "list_item_open":
            i += 1
            continue
        start, end = _lines(toks[i])
        i += 1
        children: list[Block] = []
        while toks[i].type != "list_item_close":
            got, i = _block(toks, i, depth + 1)
            children += got
        i += 1
        item = Block(
            "list_item", start, end, depth=depth, ordered=ordered, children=children
        )
        # The item's own text is its first paragraph.
        if children and children[0].kind == "paragraph":
            first = children.pop(0)
            item.text, item.code_spans, item.links = (
                first.text,
                first.code_spans,
                first.links,
            )
        items.append(item)
    return items, i + 1


def _block(toks: list[Token], i: int, depth: int = 0) -> tuple[list[Block], int]:
    """Parse one block-level construct starting at toks[i]."""
    t = toks[i]
    kind = t.type
    if kind in ("paragraph_open", "heading_open"):
        blk, i = _paragraphish(toks, i)
        return [blk], i
    if kind == "fence":
        start, end = _lines(t)
        return [Block("fence", start, end, text=t.content, info=t.info.strip())], i + 1
    if kind == "code_block":
        start, end = _lines(t)
        return [Block("code_block", start, end, text=t.content)], i + 1
    if kind == "html_block":
        start, end = _lines(t)
        return [Block("html_block", start, end, text=t.content)], i + 1
    if kind == "table_open":
        blk, i = _table(toks, i)
        return [blk], i
    if kind in ("bullet_list_open", "ordered_list_open"):
        return _list(toks, i, depth)
    if kind == "blockquote_open":
        out: list[Block] = []
        i += 1
        while toks[i].type != "blockquote_close":
            got, i = _block(toks, i, depth)
            out += got
        return out, i + 1
    return [], i + 1  # hr and anything else carries no README content


def _strip_tags(s: str) -> str:
    return " ".join(_TAG.sub("", s).split())


def parse(source: str) -> Section:
    """Parse Markdown text into a root Section (level 0) holding the whole tree."""
    source = source.removeprefix("﻿")
    total = max(1, len(source.splitlines()))
    toks = _MD.parse(source)

    root = Section("", 0, 1, total, kind="root", path=[])
    stack: list[Section] = [root]

    def close_to(index: int, end_line: int) -> None:
        while len(stack) > index:
            stack.pop().line_end = max(end_line, 1)

    i = 0
    while i < len(toks):
        t = toks[i]
        if t.type == "heading_open":
            start, _ = _lines(t)
            level = int(t.tag[1])
            title = _inline(toks[i + 1])[0].strip()
            # Pop finished sections, but never past an open <details> barrier.
            while (
                len(stack) > 1
                and stack[-1].kind != "details"
                and stack[-1].level >= level
            ):
                stack.pop().line_end = start - 1
            parent = stack[-1]
            sec = Section(title, level, start, total, path=[*parent.path, title])
            parent.children.append(sec)
            stack.append(sec)
            i += 3
            continue

        if t.type == "html_block":
            start, end = _lines(t)
            content = t.content
            opens = len(_DETAILS_OPEN.findall(content))
            closes = len(_DETAILS_CLOSE.findall(content))
            if opens > closes:
                m = _SUMMARY.search(content)
                title = _strip_tags(m.group(1)) if m else "details"
                parent = stack[-1]
                sec = Section(
                    title or "details",
                    parent.level + 1,
                    start,
                    total,
                    kind="details",
                    path=[*parent.path, title or "details"],
                )
                parent.children.append(sec)
                stack.append(sec)
                i += 1
                continue
            if closes > opens:
                for idx in range(len(stack) - 1, 0, -1):
                    if stack[idx].kind == "details":
                        close_to(idx, end)
                        break
                else:
                    stack[-1].blocks.append(
                        Block("html_block", start, end, text=content)
                    )
                i += 1
                continue

        blocks, i = _block(toks, i)
        stack[-1].blocks += blocks

    close_to(0, total)
    root.line_end = total
    return root
