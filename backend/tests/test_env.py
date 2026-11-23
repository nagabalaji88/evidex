""".env settings, endpoints, the remote guard, and the OpenAI-compatible provider."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.answer.schema import ANSWER_SCHEMA
from app.config import DEFAULT_CONFIG_PATH, ConfigError, load_config, with_env
from app.env import Endpoint, is_local_url, parse_env_text
from app.llm import make_clients
from app.main import create_app
from app.ollama_client import OllamaClient, OllamaError
from app.openai_client import OpenAICompatClient

from .conftest import SAMPLE_DOCS, FakeOllama, FakeReranker, ScriptedLLM, answer_with, config_dict
from .test_documents_api import parse_sse, upload

KEY = "sk-test-SECRET-123"


# -- .env parsing and precedence ------------------------------------------------------------


def test_parse_env_text():
    text = """
    # comment
    export EVIDEX_ANSWER_MODEL=llama3.1:8b
    EVIDEX_LLM_BASE_URL=http://localhost:1234/v1   # LM Studio
    EVIDEX_EMBEDDING_QUERY_PREFIX=""
    EVIDEX_EMBEDDING_DOC_PREFIX="search_document: "
    QUOTED='a # not a comment'
    not a line
    """
    env = parse_env_text(text)
    assert env["EVIDEX_ANSWER_MODEL"] == "llama3.1:8b"
    assert env["EVIDEX_LLM_BASE_URL"] == "http://localhost:1234/v1"
    assert env["EVIDEX_EMBEDDING_QUERY_PREFIX"] == ""
    assert env["EVIDEX_EMBEDDING_DOC_PREFIX"] == "search_document: "
    assert env["QUOTED"] == "a # not a comment"
    assert "not a line" not in "".join(env)


def write_cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    return p


def test_env_file_overrides_config_and_real_env_wins(tmp_path, monkeypatch):
    cfg_path = write_cfg(tmp_path)
    env = tmp_path / ".env"
    env.write_text("EVIDEX_ANSWER_MODEL=from-file\nEVIDEX_EMBEDDING_MODEL=bge-m3\nEVIDEX_EMBEDDING_QUERY_PREFIX=\n"
                   "EVIDEX_RERANKER_ENABLED=false\nEVIDEX_SQLITE=./elsewhere/app.db\nEVIDEX_NUM_CTX=8192\n",
                   encoding="utf-8")
    monkeypatch.setenv("EVIDEX_ENV_FILE", str(env))
    monkeypatch.setenv("EVIDEX_ANSWER_MODEL", "from-real-env")
    cfg = load_config(cfg_path)
    assert cfg.models.answer == "from-real-env"
    assert cfg.models.embedding == "bge-m3" and cfg.models.embedding_query_prefix == ""
    assert cfg.models.embedding_doc_prefix == "search_document: "  # untouched
    assert cfg.reranker.enabled is False and cfg.ollama.num_ctx == 8192
    assert cfg.sqlite_path == (tmp_path / "elsewhere" / "app.db").resolve()
    assert set(cfg.env_overrides) == {"models.answer", "models.embedding", "models.embedding_query_prefix",
                                      "reranker.enabled", "storage.sqlite", "ollama.num_ctx"}
    assert "config.yaml" and "from-real-env" not in cfg_path.read_text(encoding="utf-8")  # file never written


def test_no_env_means_local_ollama():
    cfg = with_env(config_dict(), {})
    assert cfg.llm == cfg.embed == Endpoint(provider="ollama", base_url="http://localhost:11434")
    assert cfg.env_overrides == ()


def test_openai_provider_with_separate_embeddings():
    cfg = with_env(config_dict(), {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "http://localhost:1234/v1/",
                                   "EVIDEX_LLM_API_KEY": KEY, "EVIDEX_EMBED_PROVIDER": "ollama",
                                   "EVIDEX_EMBED_BASE_URL": "http://127.0.0.1:11434"})
    assert cfg.llm.provider == "openai" and cfg.llm.base_url == "http://localhost:1234/v1"
    assert cfg.embed.provider == "ollama" and cfg.embed.api_key is None
    llm, emb = make_clients(cfg)
    assert isinstance(llm, OpenAICompatClient) and isinstance(emb, OllamaClient)
    asyncio.run(llm.aclose()); asyncio.run(emb.aclose())  # noqa: E702


def test_embeddings_default_to_the_answer_endpoint_and_key():
    cfg = with_env(config_dict(), {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "http://localhost:1234/v1",
                                   "EVIDEX_LLM_API_KEY": KEY})
    assert cfg.embed == cfg.llm
    llm, emb = make_clients(cfg)
    assert llm is emb
    asyncio.run(llm.aclose())


@pytest.mark.parametrize("env,msg", [
    ({"EVIDEX_LLM_PROVIDER": "anthropic"}, "must be 'ollama' or 'openai'"),
    ({"EVIDEX_LLM_PROVIDER": "openai"}, "EVIDEX_LLM_BASE_URL is required"),
    ({"EVIDEX_LLM_BASE_URL": "localhost:1234"}, "must start with http"),
    ({"EVIDEX_RERANKER_ENABLED": "maybe"}, "true or false"),
    ({"EVIDEX_NUM_CTX": "lots"}, "must be a number"),
    ({"EVIDEX_NUM_CTX": "99999"}, "num_ctx"),
])
def test_invalid_env_values_are_refused(env, msg):
    with pytest.raises(ConfigError, match=msg):
        with_env(config_dict(), env)


# -- the remote guard (CLAUDE.md: everything runs locally) ----------------------------------


@pytest.mark.parametrize("url,local", [
    ("http://localhost:11434", True), ("http://127.0.0.1:1234/v1", True), ("http://192.168.1.20:8080/v1", True),
    ("http://10.0.0.5:11434", True), ("http://gpu-box.local:11434", True), ("http://host.docker.internal:1234", True),
    ("https://api.openai.com/v1", False), ("https://openrouter.ai/api/v1", False), ("http://8.8.8.8:80", False),
])
def test_is_local_url(url, local):
    assert is_local_url(url) is local


def test_remote_endpoint_refused_unless_allowed():
    remote = {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "https://api.openai.com/v1",
              "EVIDEX_LLM_API_KEY": KEY}
    with pytest.raises(ConfigError, match="remote host .*EVIDEX_ALLOW_REMOTE=true"):
        with_env(config_dict(), remote)
    with pytest.raises(ConfigError, match="remote host"):
        with_env(config_dict(), {"EVIDEX_OLLAMA_BASE_URL": "http://ollama.example.com:11434"})
    cfg = with_env(config_dict(), {**remote, "EVIDEX_ALLOW_REMOTE": "true"})
    assert cfg.llm.host == "api.openai.com"


def test_api_key_never_leaks():
    cfg = with_env(config_dict(), {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "http://localhost:1234/v1",
                                   "EVIDEX_LLM_API_KEY": KEY})
    assert KEY not in json.dumps(cfg.model_dump(mode="json"))
    assert KEY not in repr(cfg) and KEY not in str(cfg.llm) and KEY not in json.dumps(cfg.llm.public())
    assert cfg.llm.public()["api_key_set"] is True


# -- OpenAI-compatible client ----------------------------------------------------------------


class FakeOpenAI:
    """An OpenAI-compatible /v1 server. `reply(pseudo_body)` uses ScriptedLLM's body shape."""

    def __init__(self, reply=None, models=("qwen2.5-7b-instruct", "nomic-embed-text"), key=None,
                 modes=("json_schema", "json_object", "none"), fences=False, reject_status=400):
        self.reply, self.models, self.key, self.modes, self.fences = reply, list(models), key, modes, fences
        self.reject_status = reject_status
        self.requests: list[tuple[str, dict, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append((request.url.path, body, dict(request.headers)))
        if self.key and request.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})
        path = request.url.path
        if path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]})
        if path.endswith("/embeddings"):
            from .conftest import FakeEmbedder
            vecs = [FakeEmbedder().vector(t) for t in body["input"]]
            data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vecs)]
            return httpx.Response(200, json={"data": list(reversed(data)), "model": body["model"]})
        if path.endswith("/chat/completions"):
            fmt = body.get("response_format") or {}
            rf = fmt.get("type", "none") + ("_schema" if fmt.get("type") == "json_object" and "schema" in fmt else "")
            if rf not in self.modes:
                return httpx.Response(self.reject_status,
                                      json={"error": {"message": f"response_format {rf} is not supported"}})
            if rf == "json_object_schema":
                body = {**body, "response_format": {"type": "json_schema", "json_schema": {"schema": fmt["schema"]}}}
            schema = (body.get("response_format") or {}).get("json_schema", {}).get("schema")
            if schema is None and body["messages"][0]["role"] == "system":
                schema = json.loads(body["messages"][0]["content"].split("this JSON schema: ", 1)[1])
            user = next(m for m in body["messages"] if m["role"] == "user")
            content = self.reply({"format": schema, "messages": [user]}) if self.reply else "{}"
            if self.fences:
                content = f"```json\n{content}\n```"
            return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"role": "assistant",
                                                                                              "content": content}}],
                                             "usage": {"prompt_tokens": 900, "completion_tokens": 40}})
        return httpx.Response(404, json={"error": "not found"})

    @property
    def transport(self):
        return httpx.MockTransport(self.handler)

    def calls(self, suffix):
        return [(b, h) for p, b, h in self.requests if p.endswith(suffix)]


def openai_cfg(key=KEY, **extra):
    return with_env(config_dict(), {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "http://localhost:1234/v1",
                                    "EVIDEX_LLM_API_KEY": key or "", "EVIDEX_ANSWER_MODEL": "qwen2.5-7b-instruct",
                                    "EVIDEX_EMBEDDING_QUERY_PREFIX": "", "EVIDEX_EMBEDDING_DOC_PREFIX": "", **extra})


def run(coro):
    return asyncio.run(coro)


def test_openai_chat_sends_schema_key_and_limits():
    fake = FakeOpenAI(reply=lambda b: json.dumps({"found_in_document": False, "sentences": [], "missing": ""}), key=KEY)
    cfg = openai_cfg()

    async def go():
        async with OpenAICompatClient(cfg, cfg.llm, transport=fake.transport) as c:
            return await c.chat([{"role": "user", "content": "q"}], schema=ANSWER_SCHEMA)

    out = run(go())
    assert json.loads(out.content)["found_in_document"] is False and out.prompt_tokens == 900
    [(body, headers)] = fake.calls("/chat/completions")
    assert headers["authorization"] == f"Bearer {KEY}"
    assert body["response_format"]["json_schema"]["schema"] == ANSWER_SCHEMA
    assert body["model"] == "qwen2.5-7b-instruct"
    assert body["temperature"] == cfg.ollama.temperature and body["max_tokens"] == cfg.ollama.num_predict
    assert fake.requests[0][0] == "/v1/chat/completions"


def test_openai_falls_back_when_json_schema_unsupported():
    fake = FakeOpenAI(reply=lambda b: '{"label": "lookup"}', modes=("none",), fences=True)
    cfg = openai_cfg(key=None)

    async def go():
        async with OpenAICompatClient(cfg, cfg.llm, transport=fake.transport) as c:
            a = await c.chat([{"role": "user", "content": "q"}], schema={"type": "object", "properties": {"label": {}}})
            b = await c.chat([{"role": "user", "content": "q"}], schema={"type": "object", "properties": {"label": {}}})
            return a, b

    a, b = run(go())
    assert json.loads(a.content) == json.loads(b.content) == {"label": "lookup"}  # fences stripped
    kinds = [(x.get("response_format") or {}).get("type", "none") + ("+schema" if "schema" in (x.get("response_format") or {}) else "")
             for x, _ in fake.calls("/chat/completions")]
    assert kinds == ["json_schema", "json_object+schema", "json_object", "none", "none"]  # working mode remembered
    assert "authorization" not in fake.calls("/chat/completions")[0][1]  # no key -> no header


def test_openai_embeddings_models_and_errors():
    fake = FakeOpenAI(key=KEY)
    cfg = openai_cfg()

    async def go():
        async with OpenAICompatClient(cfg, cfg.llm, transport=fake.transport) as c:
            vecs = await c.embed_documents(["alpha beta", "gamma", "delta"])
            names = [m["name"] for m in await c.list_models()]
            return vecs, names

    vecs, names = run(go())
    from .conftest import FakeEmbedder
    assert vecs[0] == FakeEmbedder().vector("alpha beta")  # returned out of order, sorted by index
    assert names == ["qwen2.5-7b-instruct", "nomic-embed-text"]
    assert fake.calls("/embeddings")[0][0]["input"] == ["alpha beta", "gamma", "delta"]  # prefixes set empty

    bad = openai_cfg(key="wrong-key")

    async def refused():
        async with OpenAICompatClient(bad, bad.llm, transport=fake.transport) as c:
            await c.list_models()

    with pytest.raises(OllamaError) as exc:
        run(refused())
    assert "check the API key in .env" in str(exc.value) and "wrong-key" not in str(exc.value)


def test_ollama_with_api_key_sends_bearer_header():
    fake = FakeOllama()
    cfg = with_env(config_dict(), {"EVIDEX_LLM_API_KEY": KEY})

    async def go():
        async with OllamaClient(cfg, transport=fake.transport) as c:
            await c.list_models()

    seen = []
    orig = fake.handler
    fake.handler = lambda r: (seen.append(r.headers.get("authorization")), orig(r))[1]
    run(go())
    assert seen == [f"Bearer {KEY}"]


# -- the whole app on an OpenAI-compatible server ------------------------------------------------


def test_app_end_to_end_on_openai_compatible_server(tmp_path):
    script = ScriptedLLM(answer=answer_with(("The daily allowance is INR 2,500 in metro cities.",
                                              "The daily allowance is INR 2,500")))
    fake = FakeOpenAI(reply=script, key=KEY)
    cfg = openai_cfg().model_copy(update={"base_dir": tmp_path})
    app = create_app(cfg, ollama_transport=fake.transport, reranker_loader=FakeReranker)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["ok"] is True, h
        assert h["provider"] == "OpenAI-compatible · localhost:1234" and "available" in h["answer_model"]["detail"]
        events = upload(c, SAMPLE_DOCS / "employee_handbook.pdf")
        assert events[-2][1]["status"] == "indexed"
        ans = parse_sse(c.post("/api/ask", json={"question": "What is the daily allowance in metro cities?",
                                                  "session_id": "s"}).text)[-2][1]
        assert ans["kind"] == "text" and ans["evidence"][0]["page"] == 2 and ans["model"] == "qwen2.5-7b-instruct"
        assert not ans["not_evaluated"]
        s = c.get("/api/settings").json()
        assert s["llm"]["provider"] == "openai" and s["llm"]["api_key_set"] is True
        assert "models.answer" in s["env_overrides"]
        assert KEY not in json.dumps(s) and KEY not in json.dumps(h)
        models = c.get("/api/models").json()
        assert [m["name"] for m in models["models"]][0] == "qwen2.5-7b-instruct"
    assert fake.calls("/embeddings") and fake.calls("/chat/completions")
    assert all(h["authorization"] == f"Bearer {KEY}" for _, h in fake.calls("/chat/completions"))


def test_app_with_openai_answers_and_ollama_embeddings(tmp_path):
    script = ScriptedLLM(answer=answer_with(("The daily allowance is INR 2,500 in metro cities.",
                                              "The daily allowance is INR 2,500")))
    oa = FakeOpenAI(reply=script)
    ol = FakeOllama()

    def route(request):
        return oa.handler(request) if request.url.path.startswith("/v1/") else ol.handler(request)

    cfg = with_env(config_dict(), {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "http://localhost:1234/v1",
                                   "EVIDEX_ANSWER_MODEL": "qwen2.5-7b-instruct", "EVIDEX_EMBED_PROVIDER": "ollama",
                                   "EVIDEX_EMBED_BASE_URL": "http://localhost:11434"},
                   base_dir=tmp_path)
    app = create_app(cfg, ollama_transport=httpx.MockTransport(route), reranker_loader=FakeReranker)
    with TestClient(app) as c:
        h = c.get("/api/health").json()
        assert h["ok"] and h["embed_provider"] == "Ollama" and h["provider"].startswith("OpenAI-compatible")
        upload(c, SAMPLE_DOCS / "employee_handbook.pdf")
        ans = parse_sse(c.post("/api/ask", json={"question": "What is the daily allowance in metro cities?",
                                                  "session_id": "s"}).text)[-2][1]
    assert ans["kind"] == "text"
    assert ol.calls("/api/embed") and not oa.calls("/embeddings")  # embeddings went to Ollama
    assert oa.calls("/chat/completions") and not ol.calls("/api/chat")  # answers went to the OpenAI server
    assert all(b["options"]["num_ctx"] == cfg.ollama.num_ctx for b in ol.calls("/api/embed"))


def test_key_is_not_sent_to_a_different_embedding_server():
    base = {"EVIDEX_LLM_PROVIDER": "openai", "EVIDEX_LLM_BASE_URL": "http://localhost:1234/v1", "EVIDEX_LLM_API_KEY": KEY}
    other = with_env(config_dict(), {**base, "EVIDEX_EMBED_PROVIDER": "ollama",
                                     "EVIDEX_EMBED_BASE_URL": "http://localhost:11434"})
    assert other.embed.api_key is None
    same = with_env(config_dict(), {**base, "EVIDEX_EMBED_PROVIDER": "openai"})  # same server, no URL given
    assert same.embed.api_key is not None and same.embed.api_key.get_secret_value() == KEY


def test_llama_cpp_style_server_gets_schema_via_json_object():
    """llama.cpp rejects json_schema with HTTP 500 but enforces {"type": "json_object", "schema": ...}."""
    fake = FakeOpenAI(reply=lambda b: json.dumps({"label": "lookup"}), modes=("json_object_schema", "json_object", "none"),
                      reject_status=500)
    cfg = openai_cfg(key=None)
    schema = {"type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}

    async def go():
        async with OpenAICompatClient(cfg, cfg.llm, transport=fake.transport) as c:
            return await c.chat([{"role": "user", "content": "q"}], schema=schema)

    out = run(go())
    assert json.loads(out.content) == {"label": "lookup"}
    last = fake.calls("/chat/completions")[-1][0]["response_format"]
    assert last == {"type": "json_object", "schema": schema}
