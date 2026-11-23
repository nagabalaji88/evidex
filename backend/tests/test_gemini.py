"""The Google Gemini API: config and the Settings toggle, the client (OpenAI-compatible chat
and embeddings, native model list, free-tier filter, 429 retries), the key and model
endpoints, and a whole upload-and-ask run. A fake Gemini server stands in for Google."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, with_env
from app.env import env_file_path
from app.gemini_client import GeminiClient, list_gemini_models
from app.llm import make_clients
from app.main import create_app
from app.ollama_client import OllamaError

from .conftest import SAMPLE_DOCS, FakeOllama, FakeReranker, ScriptedLLM, answer_with, config_dict
from .test_documents_api import parse_sse, upload
from .test_env import FakeOpenAI

KEY = "AIzaTestKey-0123456789abcdefghij"
OTHER = "AIzaOtherKey-9876543210zyxwvutsrq"

# What Google's model list returns (shape from aistudio.google.com/api-reference/models).
NATIVE_MODELS = [
    {"name": "models/gemini-3.5-flash-lite", "displayName": "Gemini 3.5 Flash-Lite",
     "supportedGenerationMethods": ["generateContent", "countTokens"], "inputTokenLimit": 1048576,
     "outputTokenLimit": 65536},
    {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash",
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-4-carbon", "displayName": "Gemini 4 Carbon",  # paid only
     "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemma-4-27b-it", "displayName": "Gemma 4 27B", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-embedding-001", "displayName": "Gemini Embedding 001",
     "supportedGenerationMethods": ["embedContent", "countTokens"]},
    {"name": "models/gemini-3.8-live", "displayName": "Live", "supportedGenerationMethods": ["bidiGenerateContent"]},
]


class FakeGemini:
    """/v1beta/models: the native list (2 pages, x-goog-api-key). /v1beta/openai/*: the
    OpenAI-compatible endpoint (Bearer key). `limited` 429 answers come first."""

    def __init__(self, reply=None, key=KEY, limited=0, refuse_reasoning=False):
        self.openai = FakeOpenAI(reply=reply, key=key, models=())
        self.key, self.limited, self.refuse_reasoning = key, limited, refuse_reasoning
        self.native: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1beta/models":
            self.native.append(request)
            if request.headers.get("x-goog-api-key") != self.key:
                return httpx.Response(400, json={"error": {"message": "API key not valid.", "status": "INVALID_ARGUMENT"}})
            page = request.url.params.get("pageToken")
            half = len(NATIVE_MODELS) // 2
            if page == "p2":
                return httpx.Response(200, json={"models": NATIVE_MODELS[half:]})
            return httpx.Response(200, json={"models": NATIVE_MODELS[:half], "nextPageToken": "p2"})
        if path.startswith("/v1beta/openai/"):
            if self.limited:
                self.limited -= 1
                return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
                    {"violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
                    {"retryDelay": "1s"}]}})
            body = json.loads(request.content) if request.content else {}
            if self.refuse_reasoning and "reasoning_effort" in body:
                return httpx.Response(400, json={"error": {"message": "Thinking level is not supported for this model."}})
            return self.openai.handler(request)
        return httpx.Response(404, json={"error": "not found"})

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)

    def calls(self, suffix):
        return self.openai.calls(suffix)


def gemini_text(**gemini) -> str:
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace("rate_limit_wait_seconds: 20",
                                                                    "rate_limit_wait_seconds: 0.01")
    for k, v in gemini.items():
        text = text.replace(f"  {k}: ", f"  {k}: {v}  # was: ", 1)
    return text


def gemini_cfg(tmp_path, env=None, **gemini):
    path = tmp_path / "config.yaml"
    path.write_text(gemini_text(**gemini), encoding="utf-8")
    import yaml
    return with_env(yaml.safe_load(path.read_text(encoding="utf-8")), env if env is not None else
                    {"EVIDEX_GEMINI_API_KEY": KEY}, base_dir=tmp_path), path


def run(coro):
    return asyncio.run(coro)


# -- config -----------------------------------------------------------------------------------


def test_toggle_off_uses_gemini_models_endpoint_and_key(tmp_path):
    cfg, _ = gemini_cfg(tmp_path)
    assert cfg.uses_gemini and cfg.llm.provider == "gemini" and cfg.embed == cfg.llm
    assert cfg.llm.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert cfg.llm.api_key.get_secret_value() == KEY and cfg.llm.label == "Google Gemini API"
    assert (cfg.models.answer, cfg.models.embedding) == (cfg.gemini.answer_model, cfg.gemini.embedding_model)
    assert cfg.models.embedding_query_prefix == "" and cfg.models.embedding_doc_prefix == ""
    assert cfg.local_models.answer == "qwen2.5:7b-instruct"  # kept for the toggle
    assert cfg.local_llm.provider == "ollama"


def test_key_from_googles_variable_name_and_none(tmp_path):
    cfg, _ = gemini_cfg(tmp_path, env={"GEMINI_API_KEY": OTHER})
    assert cfg.llm.api_key.get_secret_value() == OTHER
    cfg, _ = gemini_cfg(tmp_path, env={"GEMINI_API_KEY": OTHER, "EVIDEX_GEMINI_API_KEY": KEY})
    assert cfg.llm.api_key.get_secret_value() == KEY  # the one the Settings page saves wins
    cfg, _ = gemini_cfg(tmp_path, env={})
    assert cfg.llm.api_key is None


def test_toggle_on_keeps_local_models(tmp_path):
    data = config_dict()
    data["provider"]["use_local_models"] = True
    cfg = with_env(data, {"EVIDEX_GEMINI_API_KEY": KEY})
    assert not cfg.uses_gemini and cfg.llm.provider == "ollama" and cfg.models.answer == "qwen2.5:7b-instruct"


def test_paid_only_model_is_refused_while_free_tier_only(tmp_path):
    with pytest.raises(ConfigError, match="has no free tier"):
        gemini_cfg(tmp_path, answer_model="gemini-4-carbon")
    cfg, _ = gemini_cfg(tmp_path, answer_model="gemma-4-27b-it")  # matches the gemma-4* pattern
    assert cfg.models.answer == "gemma-4-27b-it"
    cfg, _ = gemini_cfg(tmp_path, answer_model="gemini-4-carbon", free_tier_only="false")
    assert cfg.models.answer == "gemini-4-carbon"


def test_gemini_urls_must_be_https(tmp_path):
    with pytest.raises(ConfigError, match="https"):
        gemini_cfg(tmp_path, base_url="http://generativelanguage.googleapis.com/v1beta/openai")


# -- client -----------------------------------------------------------------------------------


def test_model_list_is_paged_split_and_marked_free(tmp_path):
    cfg, _ = gemini_cfg(tmp_path)
    fake = FakeGemini()
    models = run(list_gemini_models(cfg, KEY, fake.transport))
    by = {m["name"]: m for m in models}
    assert len(fake.native) == 2 and fake.native[0].headers["x-goog-api-key"] == KEY
    assert "key" not in str(fake.native[0].url)  # the key goes in a header, not the URL
    assert by["gemini-3.5-flash-lite"]["kind"] == "answer" and by["gemini-3.5-flash-lite"]["free_tier"]
    assert by["gemini-embedding-001"]["kind"] == "embedding" and by["gemini-embedding-001"]["free_tier"]
    assert by["gemini-4-carbon"]["free_tier"] is False
    assert "gemini-3.8-live" not in by  # no generateContent / embedContent
    with pytest.raises(OllamaError, match="refused this API key"):
        run(list_gemini_models(cfg, OTHER, fake.transport))
    with pytest.raises(OllamaError, match="No Gemini API key"):
        run(list_gemini_models(cfg, "", fake.transport))


def test_chat_sends_key_schema_thinking_and_output_limit(tmp_path):
    cfg, _ = gemini_cfg(tmp_path)
    fake = FakeGemini(reply=lambda body: json.dumps({"ok": True}))

    async def go():
        llm, emb = make_clients(cfg, transport=fake.transport)
        assert isinstance(llm, GeminiClient) and emb is llm
        async with llm:
            out = await llm.chat([{"role": "user", "content": "hi"}], schema={"type": "object"})
            vecs = await llm.embed_documents(["a", "b"])
        return out, vecs

    out, vecs = run(go())
    assert json.loads(out.content) == {"ok": True} and len(vecs) == 2
    body, headers = fake.calls("/chat/completions")[0]
    assert headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == cfg.gemini.answer_model and body["reasoning_effort"] == "minimal"
    assert body["max_tokens"] == cfg.gemini.max_output_tokens
    assert body["response_format"]["type"] == "json_schema"
    emb_body, _ = fake.calls("/embeddings")[0]
    assert emb_body["model"] == "gemini-embedding-001" and emb_body["input"] == ["a", "b"]  # no nomic prefixes


def test_free_tier_429_waits_and_retries_then_explains(tmp_path):
    cfg, _ = gemini_cfg(tmp_path)

    async def chat(fake):
        async with GeminiClient(cfg, cfg.llm, fake.transport) as c:
            return await c.chat([{"role": "user", "content": "hi"}])

    ok = FakeGemini(reply=lambda body: "{}", limited=2)  # rate_limit_retries: 2
    assert run(chat(ok)).content == "{}"
    with pytest.raises(OllamaError, match="free-tier limit reached.*wait a minute"):
        run(chat(FakeGemini(reply=lambda body: "{}", limited=3)))


def test_reasoning_effort_dropped_when_a_model_refuses_it(tmp_path):
    cfg, _ = gemini_cfg(tmp_path)
    fake = FakeGemini(reply=lambda body: "{}", refuse_reasoning=True)

    async def go():
        async with GeminiClient(cfg, cfg.llm, fake.transport) as c:
            await c.chat([{"role": "user", "content": "1"}])
            await c.chat([{"role": "user", "content": "2"}])

    run(go())
    sent = [b for b, _ in fake.calls("/chat/completions")]
    assert len(sent) == 2 and all("reasoning_effort" not in b for b in sent)


# -- API and the whole app -------------------------------------------------------------------


def make_app(tmp_path, fake, env_key=True, monkeypatch=None):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(gemini_text(), encoding="utf-8")
    if env_key and monkeypatch is not None:
        monkeypatch.setenv("EVIDEX_GEMINI_API_KEY", KEY)
    cfg = load_config(cfg_path).model_copy(update={"base_dir": tmp_path})
    app = create_app(cfg, ollama_transport=fake.transport, reranker_loader=FakeReranker, config_file=cfg_path)
    return TestClient(app), cfg_path


def test_key_is_checked_saved_in_env_file_and_never_returned(tmp_path, monkeypatch):
    fake = FakeGemini()
    c, _ = make_app(tmp_path, fake, env_key=False)
    with c:
        s = c.get("/api/settings").json()
        assert s["gemini"] == {"in_use": True, "api_key_set": False, "api_key_source": None}
        assert "No Gemini API key" in c.get("/api/health").json()["ollama"]["detail"]
        r = c.put("/api/gemini/key", json={"api_key": OTHER})  # Google refuses it
        assert r.status_code == 422 and "refused this API key" in r.json()["detail"]
        assert c.put("/api/gemini/key", json={"api_key": "not a key"}).status_code == 422
        r = c.put("/api/gemini/key", json={"api_key": KEY})
        assert r.status_code == 200 and r.json()["ok"]
        assert f'EVIDEX_GEMINI_API_KEY="{KEY}"' in env_file_path().read_text(encoding="utf-8")
        s = c.get("/api/settings").json()
        assert s["gemini"]["api_key_set"] and s["gemini"]["api_key_source"] == ".env" and s["llm"]["api_key_set"]
        assert KEY not in json.dumps(s) and KEY not in json.dumps(c.get("/api/health").json())
        assert c.get("/api/health").json()["answer_model"]["ok"]  # applied without a restart
        assert c.delete("/api/gemini/key").json()["message"] == "API key removed from .env."
        assert not c.get("/api/settings").json()["gemini"]["api_key_set"]
        assert "EVIDEX_GEMINI_API_KEY" not in env_file_path().read_text(encoding="utf-8")


def test_model_endpoint_lists_free_tier_only_with_typed_or_saved_key(tmp_path, monkeypatch):
    c, _ = make_app(tmp_path, FakeGemini(), monkeypatch=monkeypatch)
    with c:
        r = c.post("/api/gemini/models", json={}).json()  # the saved key
        names = [m["name"] for m in r["models"]]
        assert r["error"] is None and r["free_tier_only"] and r["hidden_paid"] == 1
        assert "gemini-4-carbon" not in names and names[0] == "gemini-2.5-flash"  # answer models first
        assert {m["kind"] for m in r["models"]} == {"answer", "embedding"} and all(m["free_tier"] for m in r["models"])
        r = c.post("/api/gemini/models", json={"api_key": OTHER}).json()  # a typed key, not saved
        assert r["models"] == [] and "refused" in r["error"]


def test_toggle_switches_provider_live_and_asks_to_reindex(tmp_path, monkeypatch):
    fake = FakeGemini()
    ollama = FakeOllama()

    def route(request):
        return fake.handler(request) if request.url.host == "generativelanguage.googleapis.com" else \
            ollama.handler(request)

    c, cfg_path = make_app(tmp_path, type("T", (), {"transport": httpx.MockTransport(route)})(), monkeypatch=monkeypatch)
    with c:
        assert c.get("/api/health").json()["provider"] == "Google Gemini API"
        local = c.get("/api/models").json()  # local models, even while Gemini is on
        assert local["error"] is None and local["default"] == "qwen2.5:7b-instruct"
        assert "qwen2.5:7b-instruct" in [m["name"] for m in local["models"]]
        assert c.get("/api/settings").json()["config"]["models"]["answer"] == "qwen2.5:7b-instruct"
        r = c.put("/api/settings", json={"changes": {"provider.use_local_models": True}}).json()
        assert r["saved"] and r["reindex_required"] and r["rerun_evaluation"]
        assert c.get("/api/health").json()["provider"] == "Ollama"
        r = c.put("/api/settings", json={"changes": {"provider.use_local_models": False,
                                                     "gemini.answer_model": "gemini-2.5-flash"}}).json()
        assert r["saved"] and r["reindex_required"]
        assert c.app.state.config.models.answer == "gemini-2.5-flash"
        r = c.put("/api/settings", json={"changes": {"gemini.answer_model": "gemini-4-carbon"}})
        assert r.status_code == 422 and "no free tier" in r.json()["detail"]
        r = c.put("/api/settings", json={"changes": {"models.answer": "llama3.1:8b"}}).json()  # local, unused now
        assert r["saved"] and not r["reindex_required"] and c.app.state.config.models.answer == "gemini-2.5-flash"
    text = cfg_path.read_text(encoding="utf-8")
    assert "use_local_models: false" in text and "answer_model: gemini-2.5-flash" in text
    assert 'answer: "llama3.1:8b"' in text and KEY not in text


def test_upload_and_ask_through_gemini(tmp_path, monkeypatch):
    script = ScriptedLLM(answer=answer_with(("The daily allowance is INR 2,500 in metro cities.",
                                              "The daily allowance is INR 2,500")))
    fake = FakeGemini(reply=script)
    c, _ = make_app(tmp_path, fake, monkeypatch=monkeypatch)
    with c:
        h = c.get("/api/health").json()
        assert h["ok"] is True, h
        assert upload(c, SAMPLE_DOCS / "employee_handbook.pdf")[-2][1]["status"] == "indexed"
        ans = parse_sse(c.post("/api/ask", json={"question": "What is the daily allowance in metro cities?",
                                                  "session_id": "s"}).text)[-2][1]
        assert ans["kind"] == "text" and ans["evidence"][0]["page"] == 2 and ans["model"] == "gemini-3.5-flash-lite"
        assert c.app.state.db.get_meta("embedding_model") == "gemini-embedding-001"
    assert fake.calls("/embeddings") and fake.calls("/chat/completions")
