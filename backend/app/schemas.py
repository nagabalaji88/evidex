"""Pydantic response models. Every API response is defined here so it lands in
/openapi.json and the front end can generate its types (spec section 8.1)."""

from __future__ import annotations

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
