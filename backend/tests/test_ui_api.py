"""Spec section 8 endpoints behind the UI: knowledge base, uploads, stats, activity,
settings, models, and serving the built front end."""

from __future__ import annotations

import json

import yaml
from fastapi.testclient import TestClient

from app.config import DEFAULT_CONFIG_PATH, parse_config
from app.main import create_app

from .conftest import SAMPLE_DOCS, FakeEmbedder, FakeOllama, FakeReranker, ScriptedLLM, answer_with
from .test_documents_api import parse_sse, upload

V1 = SAMPLE_DOCS / "versions" / "v1" / "refund_policy.docx"
V2 = SAMPLE_DOCS / "versions" / "v2" / "refund_policy.docx"


def make_client(tmp_path, script=None, models=None, config_text=None):
    cfg_file = tmp_path / "config.yaml"
    # Local models (the Settings toggle on); the Gemini tests are in test_gemini.py.
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace("use_local_models: false", "use_local_models: true")
    cfg_file.write_text(config_text or text, encoding="utf-8")
    cfg = parse_config(yaml.safe_load(cfg_file.read_text(encoding="utf-8")), base_dir=tmp_path)
    ollama = FakeOllama(chat_reply=script, **({"models": models} if models else {}))
    app = create_app(cfg, ollama_transport=ollama.transport, reranker_loader=FakeReranker, embedder=FakeEmbedder(),
                     config_file=cfg_file)
    return TestClient(app), cfg_file


def ask(c, q, **kw):
    return parse_sse(c.post("/api/ask", json={"question": q, "session_id": "s", **kw}).text)[-2]


# -- knowledge base ---------------------------------------------------------------------------


def test_document_detail_versions_rollback_reindex_download(tmp_path):
    c, _ = make_client(tmp_path)
    with c:
        upload(c, V1)
        doc_id = parse_sse(c.post("/api/documents", files={"file": (V2.name, V2.read_bytes())},
                                  data={"department": "Customer Care", "owner": "Meera"}).text)[-2][1]["document_id"]
        d = c.get(f"/api/documents/{doc_id}").json()
        assert [v["status"] for v in d["versions"]] == ["inactive", "active"]
        assert d["summary"]["active_version"] == 2 and d["sections"] and d["sections"][0]["chunks"] >= 1
        assert any(s["name"] == "2. Return window" for s in d["sections"])
        r = c.post(f"/api/documents/{doc_id}/rollback", json={"version": 1})
        assert r.status_code == 200 and "v1 is active" in r.json()["message"]
        assert c.get(f"/api/documents/{doc_id}").json()["summary"]["active_version"] == 1
        assert c.post(f"/api/documents/{doc_id}/rollback", json={"version": 1}).status_code == 400
        r = c.post(f"/api/documents/{doc_id}/reindex")
        assert r.status_code == 200 and "re-indexed" in r.json()["message"]
        dl = c.get(f"/api/documents/{doc_id}/download")
        assert dl.status_code == 200 and dl.content == V1.read_bytes()
        assert c.get(f"/api/documents/{doc_id}/download?version=2").content == V2.read_bytes()


def test_table_document_detail_has_columns_and_sample_rows(tmp_path):
    c, _ = make_client(tmp_path)
    with c:
        upload(c, SAMPLE_DOCS / "inventory.xlsx", department="Merchandising")
        doc_id = c.get("/api/documents").json()["documents"][0]["id"]
        d = c.get(f"/api/documents/{doc_id}").json()
    assert [t["sheet"] for t in d["tables"]] == ["Stock", "Suppliers"]
    assert len(d["tables"][0]["sample_rows"]) == 3 and d["tables"][0]["columns"][0]["name"] == "SKU"


def test_recent_uploads_show_duplicate_and_rejected(tmp_path):
    c, _ = make_client(tmp_path)
    with c:
        upload(c, V1)
        upload(c, V1)
        upload(c, SAMPLE_DOCS / "broken" / "scanned_notice.pdf")
        rows = c.get("/api/uploads").json()["uploads"]
        steps = c.get(f"/api/uploads/{rows[0]['id']}").json()
    assert [r["status"] for r in rows] == ["rejected", "skipped", "active"]
    assert rows[0]["file_type"] == "PDF" and rows[2]["version"] == 1
    assert steps["events"][-1]["event"] == "error"


# -- AC: after asking 3 questions, the Dashboard and Activity log numbers update to match ------


def test_dashboard_and_activity_update_after_three_questions(tmp_path):
    script = ScriptedLLM(answer=answer_with(("The daily allowance is INR 2,500 in metro cities.",
                                              "The daily allowance is INR 2,500")))
    c, _ = make_client(tmp_path, script)
    with c:
        upload(c, SAMPLE_DOCS / "employee_handbook.pdf")
        before = c.get("/api/stats").json()
        assert before["questions"] == 0 and before["documents"] == 1
        ask(c, "What is the daily allowance in metro cities?")
        ask(c, "What is the capital of France?")
        ask(c, "Who won the 1998 World Cup?")
        st = c.get("/api/stats?days=7").json()
        act = c.get("/api/activity").json()["rows"]
        refused = c.get("/api/activity?result=refused").json()["rows"]
        uploads = c.get("/api/activity?result=upload").json()["rows"]
    assert (st["questions"], st["answered"], st["not_in_documents"], st["errors"]) == (3, 1, 2, 0)
    assert sum(d["answered"] + d["refused"] for d in st["per_day"]) == 3 and len(st["per_day"]) == 7
    assert st["most_cited"] == [{"name": "employee_handbook.pdf", "count": 1}]
    assert {g["name"] for g in st["gaps"]} == {"What is the capital of France?", "Who won the 1998 World Cup?"}
    assert sum(x["count"] for x in st["confidence"]) == 1 and st["avg_response_s"] is not None
    assert st["document_types"] == [{"name": "PDF", "count": 1}]
    assert len([r for r in act if r["kind"] == "question"]) == 3 and len(refused) == 2
    assert uploads[0]["kind"] == "upload" and uploads[0]["result"] == "indexed"
    answered = next(r for r in act if r["result"] == "answered")
    assert answered["source"] == "employee_handbook.pdf" and answered["trace_id"]


# -- AC: saving an invalid setting is refused; changing the threshold shows the re-run warning --


def test_invalid_setting_is_refused_and_file_unchanged(tmp_path):
    c, cfg_file = make_client(tmp_path)
    before = cfg_file.read_text(encoding="utf-8")
    with c:
        r = c.put("/api/settings", json={"changes": {"ollama.num_ctx": 99999}})
        assert r.status_code == 422 and "num_ctx" in r.json()["detail"]
        r = c.put("/api/settings", json={"changes": {"retrieval.nope": 1}})
        assert r.status_code == 422
        r = c.put("/api/settings", json={"changes": {"confidence.medium_from": 0.9}})
        assert r.status_code == 422
    assert cfg_file.read_text(encoding="utf-8") == before


def test_threshold_change_is_saved_with_rerun_warning_and_applied_live(tmp_path):
    c, cfg_file = make_client(tmp_path)
    with c:
        r = c.put("/api/settings", json={"changes": {"retrieval.relevance_threshold": 0.35}}).json()
        assert r["saved"] and r["rerun_evaluation"] and not r["reindex_required"]
        assert "Re-run the evaluation" in r["message"]
        assert c.get("/api/settings").json()["config"]["retrieval"]["relevance_threshold"] == 0.35
        assert c.get("/api/stats").json()["thresholds"]["relevance_threshold"] == 0.35  # live
        r = c.put("/api/settings", json={"changes": {"ollama.temperature": 0.2}}).json()
        assert r["saved"] and not r["rerun_evaluation"]
        r = c.put("/api/settings", json={"changes": {"ingestion.chunk_size_tokens": 400}}).json()
        assert r["reindex_required"]
    text = cfg_file.read_text(encoding="utf-8")
    assert "relevance_threshold: 0.35   # placeholder, set by evaluation" in text and "temperature: 0.2" in text


def test_reindex_all_after_embedding_change(tmp_path):
    c, _ = make_client(tmp_path)
    with c:
        upload(c, SAMPLE_DOCS / "store_operations_manual.md", department="Retail Operations")
        r = c.put("/api/settings", json={"changes": {"models.embedding": "bge-m3"}}).json()
        assert r["reindex_required"]
        out = c.post("/api/reindex").json()
        assert out["ok"] and "chunks" in out["message"]
        assert c.app.state.db.get_meta("embedding_model") == "bge-m3"


# -- models ----------------------------------------------------------------------------------


def test_models_lists_installed_with_flags(tmp_path):
    c, _ = make_client(tmp_path, models=["qwen2.5:7b-instruct", "nomic-embed-text:latest", "llama3.1:8b"])
    with c:
        m = c.get("/api/models").json()
    names = [x["name"] for x in m["models"]]
    assert names[0] == "qwen2.5:7b-instruct" and m["models"][0]["tested"] and m["default"] == "qwen2.5:7b-instruct"
    assert next(x for x in m["models"] if x["name"] == "nomic-embed-text:latest")["is_embedding"]


def test_models_when_ollama_down(tmp_path, config):
    app = create_app(config, ollama_transport=FakeOllama(down=True).transport, reranker_loader=FakeReranker,
                     embedder=FakeEmbedder())
    with TestClient(app) as c:
        m = c.get("/api/models").json()
        h = c.get("/api/health").json()
    assert m["models"] == [] and "Cannot reach Ollama" in m["error"] and h["ok"] is False


# -- production: FastAPI serves frontend/dist ---------------------------------------------------


def test_frontend_is_served_with_spa_fallback(tmp_path, monkeypatch):
    import app.main as main
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIST", dist)
    c, _ = make_client(tmp_path)
    with c:
        assert "id=root" in c.get("/").text
        assert "id=root" in c.get("/chat").text  # client-side route
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404
        assert c.get("/api/health").status_code == 200
        assert c.get("/../config.yaml").status_code in (200, 404) and "models:" not in c.get("/../config.yaml").text
