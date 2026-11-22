"""BM25 keyword index over active chunks (spec section 5: exact terms, codes, names).

Built in memory from SQLite and rebuilt whenever the index generation changes, which
happens inside every version switch. Retrieval (M3) fuses this with vector search.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from .sqlite import Database

_WORD = re.compile(r"\w+", re.UNICODE)
# Codes and clause numbers kept whole: HR-LV-07, 4.2, KR-S01, SCC-11
_COMPOUND = re.compile(r"\w+(?:[-./]\w+)+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    text = text.lower()
    return _WORD.findall(text) + _COMPOUND.findall(text)


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: str
    score: float
    version_id: int


class KeywordIndex:
    def __init__(self, db: Database):
        self.db = db
        self._lock = threading.Lock()
        self._generation = -1
        self._bm25: BM25Okapi | None = None
        self._ids: list[str] = []
        self._versions: list[int] = []

    def _ensure(self) -> None:
        gen = self.db.generation()
        if gen == self._generation:
            return
        rows = self.db.active_chunks()
        self._ids = [r["id"] for r in rows]
        self._versions = [int(r["version_id"]) for r in rows]
        corpus = [tokenize(r["text"]) for r in rows]
        self._bm25 = BM25Okapi(corpus) if corpus else None
        self._generation = gen

    def search(self, query: str, k: int, version_ids: set[int] | None = None) -> list[KeywordHit]:
        with self._lock:
            self._ensure()
            if self._bm25 is None or k <= 0:
                return []
            tokens = tokenize(query)
            if not tokens:
                return []
            scores = self._bm25.get_scores(tokens)
            active = set(self.db.active_version_ids())
            allowed = active if version_ids is None else active & set(version_ids)
            hits = [
                KeywordHit(cid, float(s), v)
                for cid, s, v in zip(self._ids, scores, self._versions)
                if s > 0 and v in allowed
            ]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]
