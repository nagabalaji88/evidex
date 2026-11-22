"""GET /api/health (spec section 8): Ollama reachable, models pulled, reranker loaded,
vector store opens."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import FakeOllama, FakeReranker, make_config


def client_for(config, ollama: FakeOllama, loader=FakeReranker):
    return TestClient(create_app(config, ollama_transport=ollama.transport, reranker_loader=loader))


def test_health_all_green(config, fake_ollama):
    with client_for(config, fake_ollama) as c:
        r = c.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body
    for key in ("ollama", "answer_model", "embedding_model", "reranker", "vector_store"):
        assert body[key]["ok"] is True, key
    assert config.vector_store_path.is_dir()  # real Chroma store opened in tmp dir


def test_health_when_ollama_is_down(config):
    with client_for(config, FakeOllama(down=True)) as c:
        body = c.get("/api/health").json()
    assert body["ok"] is False
    assert body["ollama"]["ok"] is False
    assert "Cannot reach Ollama" in body["ollama"]["detail"]
    assert body["answer_model"]["ok"] is False
    assert body["reranker"]["ok"] is True  # independent of Ollama


def test_health_reports_missing_model_with_pull_command(config):
    with client_for(config, FakeOllama(models=["nomic-embed-text:latest"])) as c:
        body = c.get("/api/health").json()
    assert body["ok"] is False
    assert body["answer_model"]["ok"] is False
    assert "ollama pull qwen2.5:7b-instruct" in body["answer_model"]["detail"]
    assert body["embedding_model"]["ok"] is True


def test_health_follows_model_names_in_config(tmp_path):
    cfg = make_config(tmp_path, models={"answer": "llama3.1:8b"})
    with client_for(cfg, FakeOllama(models=["llama3.1:8b", "nomic-embed-text:latest"])) as c:
        body = c.get("/api/health").json()
    assert body["ok"] is True
    assert "llama3.1:8b" in body["answer_model"]["detail"]


def test_reranker_loads_once_at_startup(config, fake_ollama):
    calls = []

    def loader(cfg):
        calls.append(cfg)
        return FakeReranker(cfg)

    with client_for(config, fake_ollama, loader) as c:
        for _ in range(3):
            assert c.get("/api/health").json()["reranker"]["ok"] is True
    assert len(calls) == 1


def test_reranker_load_failure_is_reported(config, fake_ollama):
    def broken(cfg):
        raise OSError("model files not found")

    with client_for(config, fake_ollama, broken) as c:
        body = c.get("/api/health").json()
    assert body["ok"] is False
    assert body["reranker"]["ok"] is False
    assert "model files not found" in body["reranker"]["detail"]


def test_reranker_disabled_is_not_loaded(tmp_path, fake_ollama):
    cfg = make_config(tmp_path, reranker={"enabled": False})

    def must_not_load(cfg):
        raise AssertionError("reranker loaded while disabled")

    with client_for(cfg, fake_ollama, must_not_load) as c:
        body = c.get("/api/health").json()
    assert body["ok"] is True
    assert "disabled" in body["reranker"]["detail"]


def test_vector_store_failure_is_reported(config, fake_ollama):
    def bad_store(cfg):
        raise PermissionError("read-only disk")

    app = create_app(config, ollama_transport=fake_ollama.transport, reranker_loader=FakeReranker, open_store=bad_store)
    with TestClient(app) as c:
        body = c.get("/api/health").json()
    assert body["vector_store"]["ok"] is False
    assert "read-only disk" in body["vector_store"]["detail"]


def test_health_is_in_openapi_schema(config, fake_ollama):
    with client_for(config, fake_ollama) as c:
        spec = c.get("/openapi.json").json()
    assert "/api/health" in spec["paths"]
    assert "HealthResponse" in spec["components"]["schemas"]
