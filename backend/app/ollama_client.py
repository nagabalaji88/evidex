"""Thin async client for the local Ollama server.

Rules enforced here (spec section 2):
- every call sets num_ctx explicitly, so Ollama's default context is never used;
- temperature, num_predict, keep_alive and the timeout come from config;
- answer calls pass a JSON schema in Ollama's `format` parameter;
- embeddings get the search_query / search_document prefixes from config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config


class OllamaError(RuntimeError):
    """Ollama is unreachable, timed out, or returned an error."""


@dataclass(frozen=True)
class ChatResult:
    content: str
    model: str
    prompt_tokens: int
    output_tokens: int
    total_ms: float
    prompt_eval_ms: float
    eval_ms: float

    @property
    def tokens_per_second(self) -> float:
        return self.output_tokens / (self.eval_ms / 1000) if self.eval_ms > 0 else 0.0

    @property
    def prompt_tokens_per_second(self) -> float:
        return self.prompt_tokens / (self.prompt_eval_ms / 1000) if self.prompt_eval_ms > 0 else 0.0


def model_name_matches(wanted: str, installed: str) -> bool:
    """`nomic-embed-text` matches `nomic-embed-text:latest`; tagged names must match exactly."""
    if wanted == installed:
        return True
    if ":" not in wanted:
        return installed == f"{wanted}:latest"
    return False


class OllamaClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._client = httpx.AsyncClient(
            base_url=config.ollama.base_url,
            timeout=httpx.Timeout(config.ollama.timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OllamaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # -- low level -----------------------------------------------------------------------

    def options(self, **overrides: Any) -> dict[str, Any]:
        o = self.config.ollama
        opts = {"num_ctx": o.num_ctx, "temperature": o.temperature, "num_predict": o.num_predict}
        opts.update(overrides)
        return opts

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise OllamaError(
                f"Ollama did not answer within {self.config.ollama.timeout_seconds:g} s"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.config.ollama.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise OllamaError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # -- API -------------------------------------------------------------------------------

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            resp = await self._client.get("/api/tags")
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.config.ollama.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise OllamaError(f"Ollama returned HTTP {resp.status_code}")
        return resp.json().get("models", [])

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        **option_overrides: Any,
    ) -> ChatResult:
        """Non-streaming chat call. Pass `schema` for structured (JSON schema) output."""
        payload: dict[str, Any] = {
            "model": model or self.config.models.answer,
            "messages": messages,
            "stream": False,
            "keep_alive": self.config.ollama.keep_alive,
            "options": self.options(**option_overrides),
        }
        if schema is not None:
            payload["format"] = schema
        data = await self._post("/api/chat", payload)
        ns = 1_000_000
        return ChatResult(
            content=data.get("message", {}).get("content", ""),
            model=data.get("model", payload["model"]),
            prompt_tokens=int(data.get("prompt_eval_count", 0)),
            output_tokens=int(data.get("eval_count", 0)),
            total_ms=data.get("total_duration", 0) / ns,
            prompt_eval_ms=data.get("prompt_eval_duration", 0) / ns,
            eval_ms=data.get("eval_duration", 0) / ns,
        )

    async def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """Embed raw texts (no prefix added). Prefer embed_documents / embed_query."""
        if not texts:
            return []
        payload = {
            "model": model or self.config.models.embedding,
            "input": texts,
            "keep_alive": self.config.ollama.keep_alive,
            "options": {"num_ctx": self.config.ollama.num_ctx},
        }
        data = await self._post("/api/embed", payload)
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise OllamaError("Ollama returned an unexpected embedding response")
        return vectors

    async def embed_documents(self, chunks: list[str]) -> list[list[float]]:
        """Embed chunks with the document prefix, in batches of ingestion.embed_batch_size."""
        prefix = self.config.models.embedding_doc_prefix
        size = self.config.ingestion.embed_batch_size
        out: list[list[float]] = []
        for i in range(0, len(chunks), size):
            out.extend(await self.embed([prefix + c for c in chunks[i : i + size]]))
        return out

    async def embed_query(self, question: str) -> list[float]:
        return (await self.embed([self.config.models.embedding_query_prefix + question]))[0]
