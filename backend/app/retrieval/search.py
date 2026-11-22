"""Hybrid search (spec section 5): vector search (meaning) and BM25 (exact terms) over
active chunks of the chosen documents, merged with reciprocal rank fusion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..store.keyword import KeywordIndex
from ..store.sqlite import Database
from ..store.vectors import VectorStore

# Standard reciprocal-rank-fusion constant (Cormack et al. 2009). RRF only orders the
# candidates; it is never used to decide whether an answer exists (spec section 5).
RRF_K = 60


@dataclass
class Candidate:
    chunk_id: str
    row: dict[str, Any]
    vector_similarity: float | None = None
    vector_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None
    found_by: list[str] = field(default_factory=list)

    @property
    def via(self) -> str:
        return " + ".join(self.found_by)

    @property
    def score(self) -> float:
        """The relevance score shown and used for confidence: reranker, else vector similarity."""
        if self.rerank_score is not None:
            return self.rerank_score
        return self.vector_similarity if self.vector_similarity is not None else 0.0

    def summary(self) -> dict[str, Any]:
        r = self.row
        return {
            "chunk_id": self.chunk_id, "doc_name": r["doc_name"], "version": r["version"], "page": r["page"],
            "section": r["section"], "source_location": r["source_location"], "via": self.via,
            "vector_similarity": _round(self.vector_similarity), "keyword_score": _round(self.keyword_score),
            "rrf_score": _round(self.rrf_score, 5), "rerank_score": _round(self.rerank_score),
        }


def _round(v: float | None, n: int = 4) -> float | None:
    return None if v is None else round(float(v), n)


def allowed_versions(db: Database, document_ids: list[int] | None) -> list[int]:
    """Active versions, restricted to the chosen documents (None = all active documents)."""
    if document_ids is None:
        return db.active_version_ids()
    if not document_ids:
        return []
    marks = ",".join("?" * len(document_ids))
    return [r["id"] for r in db.query(
        f"SELECT id FROM versions WHERE status = 'active' AND document_id IN ({marks})", tuple(document_ids))]


def hybrid_search(
    db: Database,
    vectors: VectorStore,
    keywords: KeywordIndex,
    query: str,
    query_embedding: list[float],
    candidate_k: int,
    version_ids: list[int],
) -> list[Candidate]:
    if not version_ids:
        return []
    vhits = vectors.search(query_embedding, candidate_k, version_ids)
    khits = keywords.search(query, candidate_k, set(version_ids))
    cands: dict[str, Candidate] = {}
    for rank, h in enumerate(vhits, 1):
        c = cands.setdefault(h.chunk_id, Candidate(h.chunk_id, {}))
        c.vector_similarity, c.vector_rank = h.similarity, rank
        c.rrf_score += 1.0 / (RRF_K + rank)
        c.found_by.append("meaning")
    for rank, h in enumerate(khits, 1):
        c = cands.setdefault(h.chunk_id, Candidate(h.chunk_id, {}))
        c.keyword_score, c.keyword_rank = h.score, rank
        c.rrf_score += 1.0 / (RRF_K + rank)
        c.found_by.append("keyword")
    rows = db.chunks_by_ids(list(cands))
    allowed = set(version_ids)
    out = []
    for c in cands.values():
        row = rows.get(c.chunk_id)
        # Final guard: SQLite must agree the chunk is active and in scope.
        if row is None or row["status"] != "active" or row["version_id"] not in allowed:
            continue
        c.row = dict(row)
        out.append(c)
    out.sort(key=lambda c: c.rrf_score, reverse=True)
    return out[:candidate_k]
