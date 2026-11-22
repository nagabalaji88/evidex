"""Health checks (spec section 8: GET /health). Shared by the API and the setup check."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .config import Config
from .ollama_client import OllamaClient, OllamaError, model_name_matches
from .reranker import Reranker
from .schemas import CheckResult, HealthResponse


@dataclass
class RerankerState:
    """Result of loading the reranker once at start-up."""

    enabled: bool
    model_name: str
    reranker: Reranker | None = None
    error: str | None = None
    load_seconds: float = 0.0

    @classmethod
    def load(cls, config: Config, loader: Callable[[Config], Reranker]) -> "RerankerState":
        state = cls(enabled=config.reranker.enabled, model_name=config.reranker.model)
        if not state.enabled:
            return state
        start = time.perf_counter()
        try:
            state.reranker = loader(config)
        except Exception as exc:  # report in /health rather than crash the server
            state.error = f"{type(exc).__name__}: {exc}"
        state.load_seconds = time.perf_counter() - start
        return state

    def check(self) -> CheckResult:
        if not self.enabled:
            return CheckResult(ok=True, detail=f"disabled in config ({self.model_name})")
        if self.reranker is not None:
            return CheckResult(ok=True, detail=f"{self.model_name} loaded in {self.load_seconds:.1f} s")
        return CheckResult(ok=False, detail=f"{self.model_name} failed to load: {self.error}")


async def check_models(config: Config, ollama: OllamaClient) -> tuple[CheckResult, CheckResult, CheckResult]:
    """Returns (ollama reachable, answer model pulled, embedding model pulled)."""
    try:
        models = await ollama.list_models()
    except OllamaError as exc:
        down = CheckResult(ok=False, detail="not checked: Ollama is unreachable")
        return CheckResult(ok=False, detail=str(exc)), down, down
    names = [m.get("name") or m.get("model", "") for m in models]
    reach = CheckResult(ok=True, detail=f"{config.ollama.base_url} ({len(names)} models installed)")

    def pulled(wanted: str) -> CheckResult:
        hit = next((n for n in names if model_name_matches(wanted, n)), None)
        if hit:
            return CheckResult(ok=True, detail=f"{hit} is pulled")
        return CheckResult(ok=False, detail=f"{wanted} is not pulled. Run: ollama pull {wanted}")

    return reach, pulled(config.models.answer), pulled(config.models.embedding)


def check_vector_store(open_store: Callable[[], object]) -> CheckResult:
    try:
        client = open_store()
        client.heartbeat()
    except Exception as exc:
        return CheckResult(ok=False, detail=f"cannot open vector store: {type(exc).__name__}: {exc}")
    return CheckResult(ok=True, detail="opens")


async def run_health_checks(
    config: Config,
    ollama: OllamaClient,
    reranker_state: RerankerState,
    open_store: Callable[[], object],
) -> HealthResponse:
    reach, answer, embedding = await check_models(config, ollama)
    rerank = reranker_state.check()
    store = check_vector_store(open_store)
    if store.ok:
        store = CheckResult(ok=True, detail=f"{config.vector_store_path} opens")
    ok = all(c.ok for c in (reach, answer, embedding, rerank, store))
    return HealthResponse(
        ok=ok, ollama=reach, answer_model=answer, embedding_model=embedding, reranker=rerank, vector_store=store
    )
