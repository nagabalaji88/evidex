"""One log row per question (spec sections 5 and 11): question, chosen documents, model,
chunks and scores, every step and check, time per step, and whether the answer model ran."""

from __future__ import annotations

import json
from typing import Any

from ..retrieval.retriever import RetrievalResult
from ..store.sqlite import Database, now_iso

_EXTRA_COLUMNS = {
    "model": "TEXT", "answer": "TEXT", "answer_json": "TEXT", "confidence": "REAL", "confidence_level": "TEXT",
    "checks": "TEXT", "total_ms": "INTEGER", "tokens": "TEXT", "error": "TEXT", "feedback": "INTEGER",
    "cited_documents": "TEXT",
}


def ensure_columns(db: Database) -> None:
    have = {r["name"] for r in db.query("PRAGMA table_info(question_log)")}
    for name, typ in _EXTRA_COLUMNS.items():
        if name not in have:
            db.conn.execute(f"ALTER TABLE question_log ADD COLUMN {name} {typ}")


def log_question(db: Database, result: RetrievalResult, *, session_id: str, outcome: str,
                 answer_llm_called: bool = False, steps: list[dict[str, Any]] | None = None,
                 **extra: Any) -> int:
    ensure_columns(db)
    g = result.gate
    row: dict[str, Any] = {
        "session_id": session_id, "created_at": now_iso(), "question": result.question,
        "standalone_question": result.standalone_question, "route": result.route.route,
        "document_ids": json.dumps(result.document_ids) if result.document_ids is not None else None,
        "gate_passed": None if g is None else int(g.passed), "best_score": None if g is None else g.best_score,
        "gate_method": None if g is None else g.method, "answer_llm_called": int(answer_llm_called),
        "llm_calls": json.dumps(result.llm_calls), "candidates": json.dumps([c.summary() for c in result.candidates]),
        "steps": json.dumps(steps if steps is not None else [s.as_dict() for s in result.steps]),
        "retrieval_ms": result.retrieval_ms, "outcome": outcome,
    }
    for k, v in extra.items():
        row[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    cols = ",".join(row)
    with db._lock:
        cur = db.conn.execute(f"INSERT INTO question_log({cols}) VALUES ({','.join('?' * len(row))})",
                              tuple(row.values()))
        return int(cur.lastrowid)
