from __future__ import annotations

from app import ollama_models
from app.config import DEFAULT_CONFIG_PATH, load_config


def test_lists_config_models_for_ollama(monkeypatch):
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert ollama_models.needed_models() == [cfg.models.answer, cfg.models.embedding]


def test_skips_models_served_by_another_provider(monkeypatch):
    monkeypatch.setenv("EVIDEX_LLM_PROVIDER", "openai")
    monkeypatch.setenv("EVIDEX_LLM_BASE_URL", "http://localhost:1234/v1")
    cfg = load_config(DEFAULT_CONFIG_PATH)
    # The embedding endpoint follows the chat endpoint unless set separately.
    assert ollama_models.needed_models() == []
    monkeypatch.setenv("EVIDEX_EMBED_PROVIDER", "ollama")
    monkeypatch.setenv("EVIDEX_EMBED_BASE_URL", "http://localhost:11434")
    assert ollama_models.needed_models() == [cfg.models.embedding]
