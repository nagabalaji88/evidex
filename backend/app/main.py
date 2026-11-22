"""FastAPI application. Every endpoint lives under /api (spec section 8.1); in production
FastAPI also serves the built React app (frontend/dist) at http://localhost:8000.

Run: cd backend; .venv\\Scripts\\python -m uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .answer.ask import AskService
from .api import ask, documents, evaluation, insights, settings
from .api.openapi import register_models
from .api.sse import EVENT_MODELS
from .config import REPO_ROOT, Config, get_config
from .health import RerankerState, run_health_checks
from .ingestion.pipeline import Embedder, Ingestor
from .ingestion.versioning import check_embedding_model, reconcile
from .logs.questions import ensure_columns
from .ollama_client import OllamaClient
from .reranker import Reranker, load_reranker
from .retrieval.retriever import Retriever
from .schemas import HealthResponse
from .store.keyword import KeywordIndex
from .store.sqlite import open_database
from .store.vectors import VectorStore, open_vector_store

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    s = request.app.state
    return await run_health_checks(s.config, s.ollama, s.reranker_state, lambda: s.vectors)


def build_services(app: FastAPI, cfg: Config) -> None:
    """(Re)create everything that holds the config. Called at start-up and after Settings save."""
    s = app.state
    old = getattr(s, "config", None)
    s.config = cfg
    if old is None or old.reranker != cfg.reranker:
        s.reranker_state = RerankerState.load(cfg, s.reranker_loader)
    if old is None or old.ollama != cfg.ollama or old.models != cfg.models:
        s.ollama = OllamaClient(cfg, transport=s.ollama_transport)
    embedder = s.embedder_override or s.ollama
    s.ingestor = Ingestor(cfg, s.db, s.vectors, embedder)
    s.retriever = Retriever(cfg, s.db, s.vectors, s.keyword_index, embedder, s.reranker_state.reranker, s.ollama)
    s.ask_service = AskService(cfg, s.db, s.retriever, s.ollama)


def create_app(
    config: Config | None = None,
    *,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    reranker_loader: Callable[[Config], Reranker] = load_reranker,
    open_store: Callable[[Config], object] = open_vector_store,
    embedder: Embedder | None = None,
    serve_frontend: bool = True,
    config_file=None,
) -> FastAPI:
    """Build the app. Tests inject a fake Ollama transport, reranker loader, store and embedder."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cfg = config or get_config()
        db = open_database(cfg)
        # Refuse to start if the index was built with another embedding model (spec section 4).
        try:
            check_embedding_model(db, cfg)
        except Exception:
            db.close()
            raise
        s = app.state
        s.db, s.ollama_transport, s.reranker_loader, s.embedder_override = db, ollama_transport, reranker_loader, embedder
        s.config_file = config_file
        s.vectors = _LazyVectors(cfg, open_store)
        if s.vectors.available():
            reconcile(db, s.vectors.store)
        s.keyword_index = KeywordIndex(db)
        ensure_columns(db)
        build_services(app, cfg)
        try:
            yield
        finally:
            await s.ollama.aclose()
            db.close()

    app = FastAPI(title="Evidex", version="1.0.0", lifespan=lifespan)
    app.state.build_services = build_services
    app.include_router(router)
    for r in (documents.router, ask.router, evaluation.router, insights.router, settings.router):
        app.include_router(r)
    register_models(app, list(EVENT_MODELS.values()))

    if serve_frontend:
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/") or path == "api":
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            dist = FRONTEND_DIST.resolve()
            target = (dist / path).resolve()
            if path and target.is_file() and dist in target.parents:
                return FileResponse(target)
            index = dist / "index.html"
            if index.is_file():
                return FileResponse(index)
            return JSONResponse({"detail": "The front end is not built. Run: cd frontend; npm install; "
                                           "npm run build"}, status_code=404)
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
