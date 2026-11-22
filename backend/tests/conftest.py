from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml

from app.config import DEFAULT_CONFIG_PATH, Config, parse_config


def config_dict() -> dict:
    return yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def make_config(tmp_path: Path, **overrides) -> Config:
    """The repo config.yaml with storage in tmp_path; overrides like ollama={"num_ctx": 2048}."""
    data = config_dict()
    for section, values in overrides.items():
        data[section].update(values)
    return parse_config(data, base_dir=tmp_path)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


class FakeOllama:
    """An httpx MockTransport standing in for the Ollama server. Records every request."""

    def __init__(self, models=("qwen2.5:7b-instruct", "nomic-embed-text:latest"), down=False):
        self.models = list(models)
        self.down = down
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in self.models]})
        if request.url.path == "/api/chat":
            content = json.dumps({"found_in_document": True, "sentences": [{"text": "x", "ids": ["S1"]}], "missing": ""})
            return httpx.Response(
                200,
                json={
                    "model": body["model"],
                    "message": {"role": "assistant", "content": content},
                    "prompt_eval_count": 1200,
                    "eval_count": 60,
                    "total_duration": 12_000_000_000,
                    "prompt_eval_duration": 4_000_000_000,
                    "eval_duration": 8_000_000_000,
                },
            )
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"model": body["model"], "embeddings": [[0.1, 0.2, 0.3] for _ in body["input"]]})
        return httpx.Response(404, json={"error": "not found"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def calls(self, path: str) -> list[dict]:
        return [b for p, b in self.requests if p == path]


@pytest.fixture
def fake_ollama() -> FakeOllama:
    return FakeOllama()


class FakeReranker:
    def __init__(self, config: Config):
        self.model_name = config.reranker.model

    def score(self, question, passages):
        return [0.5 for _ in passages]


# -- ingestion helpers ---------------------------------------------------------------------

import asyncio
import hashlib
import math
import re

from app.config import REPO_ROOT

SAMPLE_DOCS = REPO_ROOT / "eval" / "sample_docs"


class FakeEmbedder:
    """Deterministic hashed bag-of-words vectors: similar text -> similar vectors.
    Optional hooks let tests pause or fail in the middle of embedding."""

    dim = 256

    def __init__(self):
        self.calls = 0
        self.texts: list[str] = []
        self.before_batch = None  # async callable(batch_index)

    def vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def embed_documents(self, chunks):
        if self.before_batch is not None:
            await self.before_batch(self.calls)
        self.calls += 1
        self.texts.extend(chunks)
        return [self.vector(c) for c in chunks]

    async def embed_query(self, q):
        return self.vector(q)


class Env:
    """An Ingestor over a temporary SQLite + Chroma, with a fake embedder."""

    def __init__(self, config):
        from app.ingestion.pipeline import Ingestor
        from app.store.keyword import KeywordIndex
        from app.store.sqlite import Database
        from app.store.vectors import VectorStore, open_vector_store

        self.config = config
        self.db = Database(config.sqlite_path)
        self.vectors = VectorStore(open_vector_store(config))
        self.embedder = FakeEmbedder()
        self.ingestor = Ingestor(config, self.db, self.vectors, self.embedder)
        self.keywords = KeywordIndex(self.db)

    def ingest(self, path, name=None, data=None, department="Customer Care", owner="Asha"):
        async def go():
            return [ev async for ev in self.ingestor.ingest(
                name or path.name, data if data is not None else path.read_bytes(), department, owner)]

        return asyncio.run(go())

    def search(self, query, k=50):
        return self.vectors.search(self.embedder.vector(query), k, self.db.active_version_ids())

    def all_vector_ids(self):
        return sorted(self.vectors.collection.get(include=[])["ids"])

    def close(self):
        self.db.close()


@pytest.fixture
def env(config):
    e = Env(config)
    yield e
    e.close()


def result_of(events):
    return next(e for e in events if e.event in ("result", "error"))
