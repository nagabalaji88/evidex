"""Step 5 and 6: structure-aware chunking and tagging (spec section 4).

- Chunks never cross a heading; within a section they are filled paragraph by paragraph
  up to ingestion.chunk_size_tokens, with about chunk_overlap_tokens carried over.
- The doc title and section heading are prepended to every chunk.
- Every chunk gets a page (when the format has pages), a section and a source_location.
- Table rows become one sentence chunk each ("Product: Shirt, Region: South, Sales: 1200").
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from ..config import Config
from ..text import estimate_tokens, split_sentences
from .extract import Block, TableData, TableDoc, TextDoc


@dataclass
class Chunk:
    body: str
    text: str  # header + body: what is embedded and keyword-indexed
    page: int | None
    section: str | None
    source_location: str


@dataclass
class _Unit:
    text: str
    page: int | None
    line_start: int | None
    line_end: int | None
    json_path: str | None
    joiner: str  # how this unit joins the previous one inside a chunk

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


def header(title: str, section: str | None) -> str:
    return f"{title} > {section}" if section and section != title else title


def chunk_document(doc: TextDoc | TableDoc, config: Config) -> list[Chunk]:
    if isinstance(doc, TableDoc):
        return chunk_tables(doc)
    return chunk_text(doc, config)


# -- text path ----------------------------------------------------------------------------


def chunk_text(doc: TextDoc, config: Config) -> list[Chunk]:
    size = config.ingestion.chunk_size_tokens
    overlap = config.ingestion.chunk_overlap_tokens
    chunks: list[Chunk] = []
    for section, blocks in _sections(doc.blocks):
        head = header(doc.title, section)
        budget = max(size - estimate_tokens(head), size // 2)
        units = _units(blocks, budget)
        if not units:
            continue
        current: list[_Unit] = []
        used = 0
        for u in units:
            if current and used + u.tokens > budget:
                chunks.append(_make_chunk(doc, head, section, current))
                current = _overlap_tail(current, overlap)
                used = sum(x.tokens for x in current)
                if used + u.tokens > budget:  # overlap would overflow: drop it
                    current, used = [], 0
            current.append(u)
            used += u.tokens
        if current:
            chunks.append(_make_chunk(doc, head, section, current))
    return chunks


def _sections(blocks: list[Block]) -> list[tuple[str | None, list[Block]]]:
    """Split blocks on headings. A section with only its heading keeps the heading as text."""
    groups: list[tuple[str | None, list[Block]]] = []
    for b in blocks:
        if b.kind == "heading" or not groups or b.section != groups[-1][0]:
            groups.append((b.section, []))
        if b.kind != "heading":
            groups[-1][1].append(b)
    # JSON and TXT have no heading blocks; adjacent blocks with the same section merge above.
    return [(s, bs) for s, bs in groups if bs]


def _units(blocks: list[Block], budget: int) -> list[_Unit]:
    """Paragraphs as units; a paragraph larger than the budget is split into sentences,
    and a sentence larger than the budget into word windows."""
    out: list[_Unit] = []
    for b in blocks:
        joiner = "\n" if b.kind == "line" else "\n\n"
        base = dict(page=b.page, line_start=b.line_start, line_end=b.line_end, json_path=b.json_path)
        if estimate_tokens(b.text) <= budget:
            out.append(_Unit(b.text, joiner=joiner, **base))
            continue
        first = True
        for sent in split_sentences(b.text):
            pieces = [sent] if estimate_tokens(sent) <= budget else _word_windows(sent, budget)
            for piece in pieces:
                out.append(_Unit(piece, joiner=joiner if first else " ", **base))
                first = False
    return out


def _word_windows(text: str, budget: int) -> list[str]:
    words, out, cur = text.split(), [], []
    for w in words:
        if cur and estimate_tokens(" ".join(cur + [w])) > budget:
            out.append(" ".join(cur))
            cur = []
        cur.append(w)
    if cur:
        out.append(" ".join(cur))
    return out


def _overlap_tail(units: list[_Unit], overlap: int) -> list[_Unit]:
    """The trailing sentences of the previous chunk, up to `overlap` tokens."""
    if overlap <= 0:
        return []
    last = units[-1]
    sentences = split_sentences(last.text) if last.joiner != "\n" else [last.text]
    tail: list[str] = []
    used = 0
    for s in reversed(sentences):
        t = estimate_tokens(s)
        if used + t > overlap:
            break
        tail.insert(0, s)
        used += t
    if not tail:
        return []
    return [_Unit(" ".join(tail), last.page, last.line_start, last.line_end, last.json_path, last.joiner)]


def _make_chunk(doc: TextDoc, head: str, section: str | None, units: list[_Unit]) -> Chunk:
    body = units[0].text
    for u in units[1:]:
        body += u.joiner + u.text
    pages = [u.page for u in units if u.page is not None]
    page = min(pages) if pages else None
    return Chunk(
        body=body,
        text=f"{head}\n{body}",
        page=page,
        section=section,
        source_location=_text_location(doc.file_type, section, units, pages),
    )


def _text_location(file_type: str, section: str | None, units: list[_Unit], pages: list[int]) -> str:
    parts: list[str] = []
    if file_type in ("pdf", "docx"):
        lo, hi = (min(pages), max(pages)) if pages else (1, 1)
        parts.append(f"page {lo}" if lo == hi else f"pages {lo}-{hi}")
        if section:
            parts.append(f"section {section}")
    elif file_type == "json":
        paths = [u.json_path for u in units if u.json_path]
        return f"JSON path {_common_json_path(paths)}"
    else:  # txt, md
        if section:
            parts.append(f"section {section}")
        starts = [u.line_start for u in units if u.line_start is not None]
        ends = [u.line_end for u in units if u.line_end is not None]
        if starts:
            lo, hi = min(starts), max(ends)
            parts.append(f"line {lo}" if lo == hi else f"lines {lo}-{hi}")
    return ", ".join(parts) or "whole document"


def _common_json_path(paths: list[str]) -> str:
    if not paths:
        return "$"
    if len(set(paths)) == 1:
        return paths[0]
    prefix = os.path.commonprefix(paths)
    # cut back to a whole path segment
    while prefix and not (all(p == prefix or p[len(prefix)] in ".[" for p in paths)):
        prefix = prefix[:-1]
    return prefix.rstrip(".") or "$"


# -- table path ---------------------------------------------------------------------------


def format_value(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def row_sentence(columns: list[str], row: list) -> str:
    return ", ".join(f"{c}: {format_value(v)}" for c, v in zip(columns, row) if v is not None and v != "")


def table_location(file_type: str, table: TableData, row_number: int) -> str:
    if file_type == "json":
        return f"JSON path $[{row_number}]"
    if file_type == "xlsx":
        return f"sheet {table.sheet}, row {row_number}"
    return f"row {row_number}"


def chunk_tables(doc: TableDoc) -> list[Chunk]:
    chunks: list[Chunk] = []
    for t in doc.tables:
        section = f"sheet {t.sheet}" if t.sheet else None
        head = header(doc.title, section)
        for row, n in zip(t.rows, t.row_numbers):
            body = row_sentence(t.columns, row)
            if not body:
                continue
            chunks.append(Chunk(body=body, text=f"{head}\n{body}", page=None, section=t.sheet,
                                source_location=table_location(doc.file_type, t, n)))
    return chunks
