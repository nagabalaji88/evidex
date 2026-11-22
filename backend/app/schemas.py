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
