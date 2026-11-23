"""The retrieval pipeline (spec section 5), in order:

rewrite follow-ups -> route -> filter (active versions, chosen documents) -> hybrid search
-> rerank -> threshold gate -> top final_k passages.

The code, not the model, decides whether the answer exists: below the threshold the
answer model is never called.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..config import Config
from ..ollama_client import OllamaClient, OllamaError
from ..prompts import render_prompt
from ..store.keyword import KeywordIndex
from ..store.sqlite import Database
from ..store.vectors import VectorStore
from .rewrite import fallback_rewrite, format_history, needs_rewrite
from .route import RouteDecision, route_question
from .search import Candidate, allowed_versions, hybrid_search

REWRITE_SCHEMA = {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}
LABEL_SCHEMA = {"type": "object", "properties": {"label": {"type": "string", "enum": ["lookup", "calculation"]}},
                "required": ["label"]}


class QueryEmbedder(Protocol):
    async def embed_query(self, question: str) -> list[float]: ...


class RetrievalError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Step:
    title: str
    tech: str
    status: str  # done | blocked | failed | skipped
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "tech": self.tech, "status": self.status, "summary": self.summary,
                "detail": self.detail, "ms": self.ms}


@dataclass
class Gate:
    passed: bool
    best_score: float | None
    threshold: float
    method: str  # reranker | vector
    best_chunk: dict[str, Any] | None = None


@dataclass
class RetrievalResult:
    question: str
    standalone_question: str
    rewritten: bool
    route: RouteDecision
    document_ids: list[int] | None
    searched_chunks: int = 0
    searched_documents: int = 0
    candidates: list[Candidate] = field(default_factory=list)
    passages: list[Candidate] = field(default_factory=list)
    gate: Gate | None = None
    steps: list[Step] = field(default_factory=list)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieval_ms: int = 0  # search + rerank (spec: under 5 s)
    excluded_documents: list[str] = field(default_factory=list)


class Retriever:
    def __init__(self, config: Config, db: Database, vectors: VectorStore, keywords: KeywordIndex,
                 embedder: QueryEmbedder, reranker=None, llm: OllamaClient | None = None):
        self.config, self.db, self.vectors, self.keywords = config, db, vectors, keywords
        self.embedder, self.reranker, self.llm = embedder, reranker, llm

    # -- small LLM helpers ------------------------------------------------------------------

    async def _llm_json(self, purpose: str, prompt: str, schema: dict, result: RetrievalResult) -> dict | None:
        """purpose is also the task name: Settings can give "rewrite" and "route" their own model."""
        if self.llm is None:
            return None
        model = self.config.model_for(purpose)
        t = time.perf_counter()
        try:
            out = await self.llm.chat([{"role": "user", "content": prompt}], schema=schema, model=model)
            data = json.loads(out.content)
        except (OllamaError, json.JSONDecodeError, TypeError):
            data = None
        result.llm_calls.append({"purpose": purpose, "model": model,
                                 "ms": int((time.perf_counter() - t) * 1000), "ok": data is not None})
        return data if isinstance(data, dict) else None

    # -- pipeline ---------------------------------------------------------------------------

    async def retrieve(self, question: str, *, session_id: str = "",
                       document_ids: list[int] | None = None) -> RetrievalResult:
        cfg = self.config
        question = question.strip()
        result = RetrievalResult(question, question, False, RouteDecision("lookup", ""), document_ids)

        # 1. Rewrite follow-ups using the stored chat history.
        history = self.db.recent_turns(session_id, cfg.retrieval.history_turns) if session_id else []
        if needs_rewrite(question, history):
            t = time.perf_counter()
            prompt = render_prompt("rewrite_prompt.md", history=format_history(history), question=question)
            data = await self._llm_json("rewrite", prompt, REWRITE_SCHEMA, result)
            rewritten = (data or {}).get("question", "").strip()
            used_fallback = not rewritten
            result.standalone_question = rewritten or fallback_rewrite(question, history)
            result.rewritten = True
            result.steps.append(Step(
                "Using the conversation", "follow-up rewrite", "done",
                "Rewritten as a full question" if not used_fallback else "Combined with the previous question",
                {"original": question, "rewritten": result.standalone_question, "history_turns": len(history),
                 "fallback": used_fallback}, _ms(t)))
        query = result.standalone_question

        # 2. Route: lookup/explanation or calculation.
        t = time.perf_counter()
        versions = allowed_versions(self.db, document_ids)
        schemas = [s for s in self.db.active_table_schemas() if s["version_id"] in set(versions)]

        async def ask_label(q: str, sch: list[dict]) -> str | None:
            tables = "; ".join(f"{s['file']}{' / ' + s['sheet'] if s['sheet'] else ''}: "
                               + ", ".join(c["name"] for c in s["columns"]) for s in sch)
            data = await self._llm_json("route", render_prompt("route_prompt.md", tables=tables, question=q),
                                        LABEL_SCHEMA, result)
            label = (data or {}).get("label")
            return label if label in ("lookup", "calculation") else None

        result.route = await route_question(query, schemas, ask_label if self.llm is not None else None)
        r = result.route
        result.steps.append(Step(
            "Understanding the question", "routing rules · AI label" if r.used_llm else "routing rules · no AI call",
            "done",
            "A calculation, so use a spreadsheet table" if r.route == "calculation" else "A lookup, so search the documents",
            {"type": "Calculation" if r.route == "calculation" else "Lookup or explanation", "why": r.reason,
             "keywords": r.keywords, "columns": r.columns, "tables": r.tables,
             "route": "Run a query on a table" if r.route == "calculation" else "Search the text of the selected documents"},
            _ms(t)))
        if r.route == "calculation":
            return result  # the table path (M5) takes over

        # 3. Filter + hybrid search.
        t = time.perf_counter()
        result.searched_documents = len(versions)
        result.searched_chunks = self._count_chunks(versions)
        result.excluded_documents = self._excluded(document_ids)
        if not versions:
            result.gate = Gate(False, None, self._threshold(), self._method())
            result.steps.append(Step("Finding candidate passages", "search by meaning + keywords", "blocked",
                                     "No documents selected", {"excluded_documents": result.excluded_documents}, _ms(t)))
            self._gate_step(result, 0)
            return result
        qvec = await self.embedder.embed_query(query)
        result.candidates = hybrid_search(self.db, self.vectors, self.keywords, query, qvec,
                                          cfg.retrieval.candidate_k, versions)
        search_ms = _ms(t)
        result.steps.append(Step(
            "Finding candidate passages", "search by meaning + keywords", "done",
            f"{len(result.candidates)} candidates from {len(versions)} selected documents",
            {"searched_chunks": result.searched_chunks, "searched_documents": len(versions),
             "excluded_documents": result.excluded_documents,
             "candidates": [c.summary() for c in result.candidates]}, search_ms))

        # 4. Rerank.
        t = time.perf_counter()
        if cfg.reranker.enabled:
            if self.reranker is None:
                raise RetrievalError("reranker_unavailable",
                                     f"The reranker {cfg.reranker.model} is enabled in config but did not load. "
                                     "Check GET /api/health.")
            if result.candidates:
                scores = self.reranker.score(query, [c.row["text"] for c in result.candidates])
                for c, s in zip(result.candidates, scores):
                    c.rerank_score = float(s)
                result.candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        rank_ms = _ms(t)
        result.retrieval_ms = search_ms + rank_ms
        best = result.candidates[0] if result.candidates else None
        result.steps.append(Step(
            "Ranking by relevance",
            f"reranker · {cfg.reranker.model}" if cfg.reranker.enabled else "vector similarity (reranker off)", "done",
            f"Best match {self._best_score(result):.2f} · {_label(best)}" if best else "Nothing to rank",
            {"threshold": self._threshold(), "final_k": cfg.retrieval.final_k,
             "scores": [{"chunk_id": c.chunk_id, "label": _label(c), "score": round(c.score, 4)}
                        for c in result.candidates]}, rank_ms))

        # 5. Threshold gate. Never on the RRF score.
        best_score = self._best_score(result) if result.candidates else None
        passed = best_score is not None and best_score >= self._threshold()
        best_chunk = None
        if best is not None:
            top = max(result.candidates, key=lambda c: c.vector_similarity or -1) if not cfg.reranker.enabled else best
            best_chunk = top.summary()
        result.gate = Gate(passed, best_score, self._threshold(), self._method(), best_chunk)
        self._gate_step(result, 0)

        # 6. Top final_k passages to the model.
        if passed:
            result.passages = result.candidates[: cfg.retrieval.final_k]
        return result

    # -- helpers ----------------------------------------------------------------------------

    def _method(self) -> str:
        return "reranker" if self.config.reranker.enabled else "vector"

    def _threshold(self) -> float:
        r = self.config.retrieval
        return r.relevance_threshold if self.config.reranker.enabled else r.vector_threshold

    def _best_score(self, result: RetrievalResult) -> float:
        if self.config.reranker.enabled:
            return max((c.rerank_score or 0.0) for c in result.candidates)
        return max((c.vector_similarity if c.vector_similarity is not None else 0.0) for c in result.candidates)

    def _gate_step(self, result: RetrievalResult, ms: int) -> None:
        g = result.gate
        if g.best_score is None:
            summary = "No. Nothing relevant was found"
        elif g.passed:
            summary = f"Yes. {g.best_score:.2f} is above {g.threshold:.2f}"
        else:
            summary = f"No. {g.best_score:.2f} is below {g.threshold:.2f}"
        result.steps.append(Step("Is the answer in the documents?", "threshold gate",
                                 "done" if g.passed else "blocked", summary,
                                 {"best_score": g.best_score, "threshold": g.threshold, "method": g.method,
                                  "closest": g.best_chunk, "ai_called": False}, ms))

    def _count_chunks(self, versions: list[int]) -> int:
        if not versions:
            return 0
        marks = ",".join("?" * len(versions))
        return self.db.one(f"SELECT COUNT(*) AS n FROM chunks WHERE status = 'active' AND version_id IN ({marks})",
                           tuple(versions))["n"]

    def _excluded(self, document_ids: list[int] | None) -> list[str]:
        if document_ids is None:
            return []
        rows = self.db.query("SELECT d.id, d.name FROM documents d JOIN versions v ON v.document_id = d.id "
                             "AND v.status = 'active'")
        return [r["name"] for r in rows if r["id"] not in set(document_ids)]


def _label(c: Candidate | None) -> str:
    if c is None:
        return ""
    r = c.row
    where = f"p.{r['page']}" if r.get("page") else r.get("source_location", "")
    return f"{r.get('section') or r['doc_name']} · {where}" if r.get("section") else f"{r['doc_name']} · {where}"


def _ms(t: float) -> int:
    return int((time.perf_counter() - t) * 1000)
