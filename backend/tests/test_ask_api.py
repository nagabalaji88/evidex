"""POST /api/ask over HTTP (SSE) and GET /api/traces/{id}."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import SAMPLE_DOCS, FakeEmbedder, FakeOllama, FakeReranker, ScriptedLLM, answer_with
from .test_documents_api import parse_sse, upload


def test_ask_streams_answer_and_trace_is_stored(config):
    script = ScriptedLLM(answer=answer_with(("The daily allowance is INR 2,500 in metro cities.",
                                              "The daily allowance is INR 2,500")))
    app = create_app(config, ollama_transport=FakeOllama(chat_reply=script).transport, reranker_loader=FakeReranker,
                     embedder=FakeEmbedder())
    with TestClient(app) as c:
        upload(c, SAMPLE_DOCS / "employee_handbook.pdf")
        r = c.post("/api/ask", json={"question": "What is the daily allowance in metro cities?", "session_id": "s"})
        assert r.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(r.text)
        assert [e for e, _ in events][-2:] == ["answer", "done"]
        answer = events[-2][1]
        assert answer["kind"] == "text" and answer["evidence"][0]["page"] == 2
        trace = c.get(f"/api/traces/{answer['trace_id']}").json()
        assert trace["outcome"] == "answered" and len(trace["steps"]) == 7
        assert trace["answer"]["answer"] == answer["answer"]
        refusal = parse_sse(c.post("/api/ask", json={"question": "Who won the 1998 World Cup?",
                                                      "session_id": "s"}).text)[-2][1]
        assert refusal["kind"] == "refusal" and refusal["ai_called"] is False
        assert c.get("/api/traces/9999").status_code == 404


def test_ask_with_ollama_down_returns_clear_error(config):
    app = create_app(config, ollama_transport=FakeOllama(down=True).transport, reranker_loader=FakeReranker,
                     embedder=FakeEmbedder())
    with TestClient(app) as c:
        upload(c, SAMPLE_DOCS / "employee_handbook.pdf")
        # Retrieval uses the (working) fake embedder; the answer call hits the down Ollama.
        events = parse_sse(c.post("/api/ask", json={"question": "What is the daily allowance in metro cities?",
                                                    "session_id": "s"}).text)
    assert events[-2][0] == "error" and events[-2][1]["code"] == "ollama_unavailable"
    assert events[-1][0] == "done"
