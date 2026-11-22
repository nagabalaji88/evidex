"""FastAPI application. Every endpoint lives under /api (spec section 8.1).

Run: cd backend; .venv\\Scripts\\python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable

import httpx
from fastapi import APIRouter, FastAPI, Request

from .api import documents
from .api.openapi import register_models
from .api.sse import EVENT_MODELS
from .config import Config, get_config
from .health import RerankerState, run_health_checks
from .ingestion.pipeline import Embedder, Ingestor
from .ingestion.versioning import check_embedding_model, reconcile
from .ollama_client import OllamaClient
from .reranker import Reranker, load_reranker
from .schemas import HealthResponse
from .store.keyword import KeywordIndex
from .store.sqlite import open_database
from .store.vectors import VectorStore, open_vector_store

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    s = request.app.state
    return await run_health_checks(s.config, s.ollama, s.reranker_state, lambda: s.vectors)


def create_app(
    config: Config | None = None,
    *,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    reranker_loader: Callable[[Config], Reranker] = load_reranker,
    open_store: Callable[[Config], object] = open_vector_store,
    embedder: Embedder | None = None,
) -> FastAPI:
    """Build the app. Tests inject a fake Ollama transport, reranker loader, store and embedder."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = config or get_config()
        app.state.config = cfg
        db = open_database(cfg)
        # Refuse to start if the index was built with another embedding model (spec section 4).
        try:
            check_embedding_model(db, cfg)
        except Exception:
            db.close()
            raise
        app.state.db = db
        app.state.ollama = OllamaClient(cfg, transport=ollama_transport)
        app.state.reranker_state = RerankerState.load(cfg, reranker_loader)
        app.state.vectors = _LazyVectors(cfg, open_store)
        if app.state.vectors.available():
            reconcile(db, app.state.vectors.store)
        app.state.keyword_index = KeywordIndex(db)
        app.state.ingestor = Ingestor(cfg, db, app.state.vectors, embedder or app.state.ollama)
        try:
            yield
        finally:
            await app.state.ollama.aclose()
            db.close()

    app = FastAPI(title="Evidex", version="0.2.0", lifespan=lifespan)
    app.include_router(router)
    app.include_router(documents.router)
    register_models(app, list(EVENT_MODELS.values()))
    return app


class _LazyVectors:
    """Opens the vector store on first use, so a broken store shows up in /health instead
    of stopping the server; then behaves like the VectorStore it wraps."""

    def __init__(self, config: Config, open_store: Callable[[Config], object]):
        self._config, self._open, self._store, self.error = config, open_store, None, None

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore(self._open(self._config))
        return self._store

    def available(self) -> bool:
        try:
            self.store
            return True
        except Exception as exc:
            self.error = exc
            return False

    def __getattr__(self, name):
        return getattr(self.store, name)


app = create_app()
