"""POST /api/documents streams the steps as Server-Sent Events (spec section 8.1);
GET /api/documents lists active versions."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app

from .conftest import SAMPLE_DOCS, FakeEmbedder, FakeOllama, FakeReranker


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for frame in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def client(config):
    return TestClient(create_app(config, ollama_transport=FakeOllama().transport, reranker_loader=FakeReranker,
                                 embedder=FakeEmbedder()))


def upload(c, path, department="Customer Care", owner="Asha", name=None):
    with path.open("rb") as fh:
        r = c.post("/api/documents", files={"file": (name or path.name, fh)},
                   data={"department": department, "owner": owner})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def test_upload_streams_steps_then_result_then_done(config):
    with client(config) as c:
        events = upload(c, SAMPLE_DOCS / "employee_handbook.pdf")
    kinds = [e for e, _ in events]
    assert kinds[-1] == "done" and kinds[-2] == "result"
    assert [d["stage"] for e, d in events if e == "stage" and d["status"] == "running"] == [
        "validating", "extracting", "chunking", "embedding", "storing"]
    progress = [d for e, d in events if e == "progress"]
    assert progress[0]["done"] == 0 and progress[-1]["done"] == progress[-1]["total"] > 0
    result = events[-2][1]
    assert result["status"] == "indexed" and result["message"].startswith("Employee Handbook v1 indexed: 4 pages, ")
    assert "elapsed_ms" in events[-1][1]


def test_duplicate_and_scanned_messages(config):
    with client(config) as c:
        upload(c, SAMPLE_DOCS / "store_operations_manual.md", department="Retail Operations")
        dup = upload(c, SAMPLE_DOCS / "store_operations_manual.md", department="Retail Operations")
        scanned = upload(c, SAMPLE_DOCS / "broken" / "scanned_notice.pdf")
    assert dup[-2][0] == "result" and dup[-2][1]["message"].startswith("Duplicate, skipped")
    assert scanned[-2] == ("error", {"code": "scanned_pdf", "message":
                           "This PDF appears to be scanned and has no text layer. Upload a text-based PDF, "
                           "or enable OCR in config (slow)."})
    assert scanned[-1][0] == "done"


def test_client_path_is_stripped_from_filename(config):
    with client(config) as c:
        events = upload(c, SAMPLE_DOCS / "delivery_partner_faq.txt", department="Logistics",
                        name="C:\\Users\\asha\\Desktop\\delivery_partner_faq.txt")
        docs = c.get("/api/documents").json()["documents"]
    assert events[-2][1]["doc_name"] == "delivery_partner_faq.txt"
    assert docs[0]["name"] == "delivery_partner_faq.txt"


def test_list_documents_shows_active_version_and_counts(config):
    v1 = SAMPLE_DOCS / "versions" / "v1" / "refund_policy.docx"
    v2 = SAMPLE_DOCS / "versions" / "v2" / "refund_policy.docx"
    with client(config) as c:
        upload(c, v1)
        upload(c, v2, owner="Meera")
        upload(c, SAMPLE_DOCS / "sales_q3.csv", department="Finance")
        docs = {d["name"]: d for d in c.get("/api/documents").json()["documents"]}
    refund = docs["refund_policy.docx"]
    assert (refund["active_version"], refund["version_count"], refund["owner"]) == (2, 2, "Meera")
    assert refund["path"] == "text" and refund["chunks"] > 0
    sales = docs["sales_q3.csv"]
    assert (sales["path"], sales["table_rows"], sales["department"]) == ("table", 60, "Finance")


def test_event_payload_models_are_in_openapi(config):
    with client(config) as c:
        schemas = c.get("/openapi.json").json()["components"]["schemas"]
    for name in ("StageEvent", "ProgressEvent", "UploadResult", "ErrorEvent", "DoneEvent", "DocumentList"):
        assert name in schemas
