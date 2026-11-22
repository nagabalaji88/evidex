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
    """An httpx MockTransport standing in for the Ollama server. Records every request.
    `chat_reply(body) -> str` sets the assistant content for /api/chat calls."""

    def __init__(self, models=("qwen2.5:7b-instruct", "nomic-embed-text:latest"), down=False, chat_reply=None):
        self.models = list(models)
        self.down = down
        self.chat_reply = chat_reply
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body))
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "model": m} for m in self.models]})
        if request.url.path == "/api/chat":
            content = self.chat_reply(body) if self.chat_reply else json.dumps(
                {"found_in_document": True, "sentences": [{"text": "x", "ids": ["S1"]}], "missing": ""})
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


_STOP = set("a an the of to in on for and or is are was were be what which who whom how do does did i my we "
             "you your can could should would will it its this that there with by at from as about any".split())


def _content_words(text):
    import re as _re
    return {w for w in _re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", text.lower()) if w not in _STOP}


class FakeReranker:
    """Scores = share of the question's content words found in the passage (0..1)."""

    def __init__(self, config: Config):
        self.model_name = config.reranker.model
        self.calls = 0

    def score(self, question, passages):
        self.calls += 1
        q = _content_words(question)
        return [len(q & _content_words(p)) / len(q) if q else 0.0 for p in passages]


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


class ScriptedLLM:
    """chat_reply for FakeOllama: answers by the kind of call (from the JSON schema sent in
    `format`). Each kind has a queue of replies (str or dict); the last one repeats."""

    KINDS = {"found_in_document": "answer", "sql": "sql", "answer": "sql_answer", "supported": "claim",
             "label": "route", "question": "rewrite"}

    def __init__(self, **queues):
        self.queues = {k: list(v) if isinstance(v, list) else [v] for k, v in queues.items()}
        self.prompts: dict[str, list[str]] = {}

    def kind(self, body) -> str:
        props = (body.get("format") or {}).get("properties", {})
        return next((k for key, k in self.KINDS.items() if key in props), "other")

    def __call__(self, body):
        kind = self.kind(body)
        self.prompts.setdefault(kind, []).append(body["messages"][0]["content"])
        q = self.queues.get(kind)
        if not q:
            raise AssertionError(f"unexpected {kind} call")
        reply = q.pop(0) if len(q) > 1 else q[0]
        if callable(reply):
            reply = reply(body["messages"][0]["content"])
        return reply if isinstance(reply, str) else json.dumps(reply)

    def count(self, kind: str) -> int:
        return len(self.prompts.get(kind, []))


def sid(prompt: str, phrase: str) -> str:
    """The [S#] id of the numbered source sentence that contains `phrase`."""
    import re as _re
    for m in _re.finditer(r"^\[(S\d+)\] (.*)$", prompt, _re.M):
        if phrase in m.group(2):
            return m.group(1)
    raise AssertionError(f"{phrase!r} not among the numbered sentences")


def answer_with(*parts, found=True, missing=""):
    """Reply builder: parts are (text, phrase-or-id-list)."""
    def build(prompt):
        sentences = []
        for text, ref in parts:
            ids = ref if isinstance(ref, list) else [sid(prompt, ref)]
            sentences.append({"text": text, "ids": ids})
        return {"found_in_document": found, "sentences": sentences, "missing": missing}
    return build


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch, tmp_path_factory):
    """Tests never read a developer's real .env or EVIDEX_* variables."""
    import os
    monkeypatch.setenv("EVIDEX_ENV_FILE", str(tmp_path_factory.getbasetemp() / "no-such.env"))
    for k in list(os.environ):
        if k.startswith("EVIDEX_") and k != "EVIDEX_ENV_FILE":
            monkeypatch.delenv(k, raising=False)
