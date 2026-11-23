"""Build the model clients from config + .env: one for answers (chat) and one for
embeddings. They are the same object when both use the same endpoint."""

from __future__ import annotations

import httpx

from .config import Config
from .env import Endpoint
from .gemini_client import GeminiClient
from .ollama_client import ModelClient, OllamaClient
from .openai_client import OpenAICompatClient


def client_for(config: Config, endpoint: Endpoint, transport: httpx.AsyncBaseTransport | None = None) -> ModelClient:
    if endpoint.provider == "gemini":
        return GeminiClient(config, endpoint, transport=transport)
    if endpoint.provider == "openai":
        return OpenAICompatClient(config, endpoint, transport=transport)
    return OllamaClient(config, transport=transport, endpoint=endpoint)


def make_clients(config: Config, transport: httpx.AsyncBaseTransport | None = None) -> tuple[ModelClient, ModelClient]:
    llm = client_for(config, config.llm, transport)
    embedder = llm if config.embed == config.llm else client_for(config, config.embed, transport)
    return llm, embedder


async def close_clients(*clients: ModelClient) -> None:
    seen: set[int] = set()
    for c in clients:
        if c is not None and id(c) not in seen:
            seen.add(id(c))
            await c.aclose()
