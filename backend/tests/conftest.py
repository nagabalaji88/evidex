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
