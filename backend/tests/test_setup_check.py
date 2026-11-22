"""The M1 setup check / benchmark script, against a fake Ollama and reranker."""

from __future__ import annotations

import asyncio

import pytest

from app import setup_check
from app.ollama_client import OllamaClient

from .conftest import FakeOllama, FakeReranker


@pytest.fixture
def patched(monkeypatch):
    def use(fake: FakeOllama):
        monkeypatch.setattr(setup_check, "load_reranker", FakeReranker)
        monkeypatch.setattr(
            setup_check, "OllamaClient", lambda cfg: OllamaClient(cfg, transport=fake.transport)
        )

    return use


def test_full_run_measures_everything(config, patched):
    fake = FakeOllama()
    patched(fake)
    report = asyncio.run(setup_check.run(config, write=False))
    assert report.ok, report
    names = dict(report.numbers)
    assert names["Answer: generation speed"] == "7.50 tokens/s"
    assert any(k.startswith("Peak RAM") for k in names)
    assert any(k.startswith("RAM in use at idle") for k in names)
    assert any(k.startswith("Rerank:") for k in names)
    # The benchmark answer call is a real structured call: schema in `format`, num_ctx set.
    chat = fake.calls("/api/chat")
    assert chat and all("format" in b and b["options"]["num_ctx"] == config.ollama.num_ctx for b in chat)
    md = setup_check.render(report, config)
    assert "| Ollama reachable | PASS |" in md and "Sample answer output" in md


def test_slow_model_is_flagged(config, patched, monkeypatch):
    patched(FakeOllama())
    monkeypatch.setattr(setup_check, "MIN_TOKENS_PER_SECOND", 10.0)
    report = asyncio.run(setup_check.run(config, write=False))
    assert not report.ok
    assert any("review the model choice" in w for w in report.warnings)


def test_ollama_down_fails_cleanly(config, patched):
    patched(FakeOllama(down=True))
    report = asyncio.run(setup_check.run(config, write=False))
    assert not report.ok
    checks = {name: ok for name, ok, _ in report.checks}
    assert checks["Ollama reachable"] is False
    assert "| Ollama reachable | FAIL |" in setup_check.render(report, config)
