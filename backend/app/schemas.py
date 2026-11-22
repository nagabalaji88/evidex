"""Pydantic response models. Every API response is defined here so it lands in
/openapi.json and the front end can generate its types (spec section 8.1)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CheckResult(BaseModel):
    ok: bool
    detail: str


class HealthResponse(BaseModel):
    ok: bool
    ollama: CheckResult
    answer_model: CheckResult
    embedding_model: CheckResult
    reranker: CheckResult
    vector_store: CheckResult


# -- streaming events (spec section 8.1) ---------------------------------------------------


class StageEvent(BaseModel):
    """`stage`: a step starts (status running) or finishes with a summary (status done)."""

    stage: str
    detail: str
    status: Literal["running", "done"] = "running"
    data: dict[str, Any] | None = None


class ProgressEvent(BaseModel):
    """`progress`: during ingestion, e.g. {"stage": "embedding", "done": 40, "total": 118}."""

    stage: str
    done: int
    total: int
    detail: str | None = None


class ErrorEvent(BaseModel):
    code: str
    message: str


class DoneEvent(BaseModel):
    """`done`: always the last event."""

    elapsed_ms: int


class UploadResult(BaseModel):
    """`result`: the end of an upload stream, before `done`."""

    status: Literal["indexed", "duplicate"]
    message: str
    document_id: int
    version_id: int
    version: int
    doc_name: str
    pages: int | None = None
    sections: int = 0
    chunks: int = 0
    table_rows: int = 0
    elapsed_ms: int


# -- documents -------------------------------------------------------------------------------


class DocumentSummary(BaseModel):
    id: int
    name: str
    title: str
    file_type: str
    path: Literal["text", "table"]
    department: str
    owner: str
    active_version: int
    version_count: int
    pages: int | None
    sections: int
    chunks: int
    table_rows: int
    size_bytes: int
    updated_at: str


class DocumentList(BaseModel):
    documents: list[DocumentSummary]


# -- ask (spec sections 3, 5, 6, 8) -----------------------------------------------------------


class AskRequest(BaseModel):
    question: str
    session_id: str
    document_ids: list[int] | None = None  # None = all active documents
    model: str | None = None  # None = models.answer from config


class TraceStep(BaseModel):
    title: str
    tech: str
    status: Literal["pending", "running", "done", "blocked", "failed", "skipped"]
    summary: str
    detail: dict[str, Any] = {}
    ms: int = 0


class CheckRow(BaseModel):
    name: str
    passed: bool
    detail: str


class Citation(BaseModel):
    id: str
    chunk_id: str
    document_id: int
    doc_name: str
    version: int
    page: int | None = None
    section: str | None = None
    source_location: str
    quote: str
    score: float


class Evidence(BaseModel):
    """One evidence block: a cited chunk with its supporting sentences highlighted."""

    ref: int
    chunk_id: str
    document_id: int
    doc_name: str
    version: int
    page: int | None = None
    section: str | None = None
    source_location: str
    chunk_text: str
    highlights: list[str]
    score: float


class AnswerSentence(BaseModel):
    text: str
    ids: list[str]
    refs: list[int]
    citations: list[Citation]


class Confidence(BaseModel):
    level: Literal["High", "Medium", "Low", "Exact"]
    score: float | None = None
    threshold: float | None = None
    high_from: float | None = None
    medium_from: float | None = None
    reason: str


class ClosestMatch(BaseModel):
    doc_name: str
    version: int
    page: int | None = None
    section: str | None = None
    source_location: str
    score: float | None = None
    threshold: float


class SqlResult(BaseModel):
    query: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    table: str
    file: str
    sheet: str | None = None
    version: int
    row_count: int
    ms: int


class AnswerPayload(BaseModel):
    """`answer` event: the final answer after grounding."""

    kind: Literal["text", "calculation", "refusal"]
    found_in_document: bool
    answer: str
    sentences: list[AnswerSentence] = []
    missing: str = ""
    partial: bool = False
    evidence: list[Evidence] = []
    confidence: Confidence | None = None
    checks: list[CheckRow] = []
    closest: ClosestMatch | None = None
    ai_called: bool
    model: str
    not_evaluated: bool = False
    sql: SqlResult | None = None
    question: str
    standalone_question: str
    trace_id: int | None = None
    elapsed_ms: int = 0


class TraceResponse(BaseModel):
    id: int
    created_at: str
    question: str
    standalone_question: str
    route: str
    outcome: str
    model: str | None = None
    steps: list[TraceStep]
    answer: AnswerPayload | None = None
    total_ms: int | None = None


# -- evaluation (spec section 7) ----------------------------------------------------------------


class EvalRunRequest(BaseModel):
    compare: bool = False  # also run reranker on/off x final_k 3/5 (slow)


class EvalCase(BaseModel):
    id: str
    category: str
    question: str
    passed: bool
    reason: str
    answer: str = ""
    kind: str = ""
    refused: bool = False
    correct: bool | None = None
    citation_ok: bool | None = None
    cited: list[str] = []
    best_score: float | None = None
    gate_passed: bool | None = None
    total_ms: int = 0
    retrieval_ms: int = 0
    ai_called: bool = False


class EvalRun(BaseModel):
    label: str
    settings: dict[str, Any]
    cases: list[EvalCase]
    metrics: dict[str, Any]
    peak_ram_gb: float
    started: str
    seconds: float


class EvalReport(BaseModel):
    created_at: str
    main: EvalRun
    comparisons: list[EvalRun]
    sweep: dict[str, Any] | None = None
    cheapest_passing: dict[str, Any] | None = None
    targets: dict[str, float]
