"""Ollama client rules (spec section 2 acceptance criteria)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.answer.schema import ANSWER_SCHEMA
from app.ollama_client import OllamaClient, OllamaError, model_name_matches

from .conftest import make_config


def run(coro):
    return asyncio.run(coro)


async def _all_calls(client: OllamaClient):
    await client.chat([{"role": "user", "content": "hi"}], schema=ANSWER_SCHEMA)
    await client.chat([{"role": "user", "content": "hi"}])
    await client.embed_documents(["a", "b"])
    await client.embed_query("q")


def test_num_ctx_is_set_on_every_call(config, fake_ollama):
    """AC: num_ctx is explicitly set on every call (Ollama's default must not be used)."""

    async def go():
        async with OllamaClient(config, transport=fake_ollama.transport) as c:
            await _all_calls(c)

    run(go())
    model_calls = [b for p, b in fake_ollama.requests if p in ("/api/chat", "/api/embed")]
    assert len(model_calls) == 4
    for body in model_calls:
        assert body["options"]["num_ctx"] == config.ollama.num_ctx
        assert body["keep_alive"] == config.ollama.keep_alive


def test_num_ctx_follows_config(tmp_path, fake_ollama):
    cfg = make_config(tmp_path, ollama={"num_ctx": 8192})

    async def go():
        async with OllamaClient(cfg, transport=fake_ollama.transport) as c:
            await _all_calls(c)

    run(go())
    assert {b["options"]["num_ctx"] for p, b in fake_ollama.requests if p != "/api/tags"} == {8192}


def test_answer_call_sends_json_schema_in_format(config, fake_ollama):
    """AC: every answer call sends the JSON schema through Ollama's format parameter."""

    async def go():
        async with OllamaClient(config, transport=fake_ollama.transport) as c:
            return await c.chat([{"role": "user", "content": "q"}], schema=ANSWER_SCHEMA)

    result = run(go())
    body = fake_ollama.calls("/api/chat")[0]
    assert body["format"] == ANSWER_SCHEMA
    assert body["stream"] is False
    assert body["options"]["temperature"] == config.ollama.temperature
    assert body["options"]["num_predict"] == config.ollama.num_predict
    assert result.tokens_per_second == pytest.approx(60 / 8)


def test_changing_model_name_in_config_switches_model(tmp_path, fake_ollama):
    """AC: changing the model name in config switches the model with no code change."""
    cfg = make_config(tmp_path, models={"answer": "llama3.1:8b", "embedding": "bge-m3"})

    async def go():
        async with OllamaClient(cfg, transport=fake_ollama.transport) as c:
            await _all_calls(c)

    run(go())
    assert {b["model"] for b in fake_ollama.calls("/api/chat")} == {"llama3.1:8b"}
    assert {b["model"] for b in fake_ollama.calls("/api/embed")} == {"bge-m3"}


def test_embedding_prefixes_and_batching(tmp_path, fake_ollama):
    """Spec section 2: search_query / search_document prefixes; embed in batches of 8."""
    cfg = make_config(tmp_path)
    chunks = [f"chunk {i}" for i in range(19)]

    async def go():
        async with OllamaClient(cfg, transport=fake_ollama.transport) as c:
            vecs = await c.embed_documents(chunks)
            q = await c.embed_query("refund window?")
            return vecs, q

    vecs, q = run(go())
    assert len(vecs) == 19 and len(q) == 3
    calls = fake_ollama.calls("/api/embed")
    batch = cfg.ingestion.embed_batch_size
    assert [len(c["input"]) for c in calls[:-1]] == [batch, batch, 19 - 2 * batch]
    assert all(t.startswith("search_document: ") for c in calls[:-1] for t in c["input"])
    assert calls[0]["input"][0] == "search_document: chunk 0"
    assert calls[-1]["input"] == ["search_query: refund window?"]


def test_unreachable_ollama_raises_clear_error(config):
    down = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused", request=r)))

    async def go():
        async with OllamaClient(config, transport=down) as c:
            await c.chat([{"role": "user", "content": "q"}])

    with pytest.raises(OllamaError, match="Cannot reach Ollama"):
        run(go())


def test_timeout_raises_clear_error(config):
    slow = httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)))

    async def go():
        async with OllamaClient(config, transport=slow) as c:
            await c.embed_query("q")

    with pytest.raises(OllamaError, match="did not answer within"):
        run(go())


def test_http_error_from_ollama(config):
    err = httpx.MockTransport(lambda r: httpx.Response(500, text="model crashed"))

    async def go():
        async with OllamaClient(config, transport=err) as c:
            await c.chat([{"role": "user", "content": "q"}])

    with pytest.raises(OllamaError, match="HTTP 500"):
        run(go())


@pytest.mark.parametrize(
    "wanted,installed,expected",
    [
        ("nomic-embed-text", "nomic-embed-text:latest", True),
        ("nomic-embed-text", "nomic-embed-text", True),
        ("qwen2.5:7b-instruct", "qwen2.5:7b-instruct", True),
        ("qwen2.5:7b-instruct", "qwen2.5:14b-instruct", False),
        ("qwen2.5:7b-instruct", "qwen2.5:latest", False),
        ("nomic-embed-text", "nomic-embed-text:v1.5", False),
    ],
)
def test_model_name_matching(wanted, installed, expected):
    assert model_name_matches(wanted, installed) is expected
