"""Numbered source sentences (spec section 3). Before the call, the selected chunks are
split into sentences numbered S1, S2, ... across all chunks, keeping a map from each ID to
its chunk, document, version and source location. The model only cites IDs; the code
attaches the quotes and locations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..text import split_sentences


@dataclass(frozen=True)
class SourceSentence:
    id: str
    text: str
    chunk_id: str
    document_id: int
    doc_name: str
    version: int
    page: int | None
    section: str | None
    source_location: str
    score: float  # relevance score of the chunk it came from


def chunk_sentences(body: str) -> list[str]:
    out: list[str] = []
    for line in body.splitlines():
        if line.strip():
            out.extend(split_sentences(line))
    return out


def number_sentences(passages) -> tuple[str, dict[str, SourceSentence]]:
    """passages: retrieval Candidates. Returns (prompt block, id -> SourceSentence)."""
    lines: list[str] = []
    smap: dict[str, SourceSentence] = {}
    n = 0
    for i, p in enumerate(passages, 1):
        r = p.row
        label = f"{r['doc_name']} v{r['version']}, {r['source_location']}"
        lines.append(f"Passage {i} ({label}):")
        for sent in chunk_sentences(r["body"]):
            n += 1
            sid = f"S{n}"
            smap[sid] = SourceSentence(sid, sent, p.chunk_id, int(r["document_id"]), r["doc_name"], int(r["version"]),
                                       r["page"], r["section"], r["source_location"], float(p.score))
            lines.append(f"[{sid}] {sent}")
        lines.append("")
    return "\n".join(lines).strip(), smap


def citation_for(s: SourceSentence) -> dict[str, Any]:
    return {"id": s.id, "chunk_id": s.chunk_id, "document_id": s.document_id, "doc_name": s.doc_name,
            "version": s.version, "page": s.page, "section": s.section, "source_location": s.source_location,
            "quote": s.text, "score": round(s.score, 4)}
