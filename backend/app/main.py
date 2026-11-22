"""FastAPI application. Every endpoint lives under /api (spec section 8.1).

Run: cd backend; .venv\\Scripts\\python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable

import httpx
from fastapi import APIRouter, FastAPI, Request

from .config import Config, get_config
from .health import RerankerState, run_health_checks
from .ollama_client import OllamaClient
from .reranker import Reranker, load_reranker
from .schemas import HealthResponse
from .store.vectors import open_vector_store

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    s = request.app.state
    return await run_health_checks(s.config, s.ollama, s.reranker_state, s.open_store)


def create_app(
    config: Config | None = None,
    *,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    reranker_loader: Callable[[Config], Reranker] = load_reranker,
    open_store: Callable[[Config], object] = open_vector_store,
) -> FastAPI:
    """Build the app. Tests inject a fake Ollama transport, reranker loader and store."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = config or get_config()
        app.state.config = cfg
        app.state.ollama = OllamaClient(cfg, transport=ollama_transport)
        # Load the reranker once at start-up when enabled (spec section 2).
        app.state.reranker_state = RerankerState.load(cfg, reranker_loader)
        store_cache: dict[str, object] = {}

        def _open():
            if "client" not in store_cache:
                store_cache["client"] = open_store(cfg)
            return store_cache["client"]

        app.state.open_store = _open
        try:
            yield
        finally:
            await app.state.ollama.aclose()

    app = FastAPI(title="Evidex", version="0.1.0", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
