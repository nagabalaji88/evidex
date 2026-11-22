"""Spec section 4 acceptance criteria, plus pipeline behaviour (validation, dedup,
confirmation, tables)."""

from __future__ import annotations

import asyncio

import pytest

from app.ingestion.errors import SCANNED_PDF_MESSAGE

from .conftest import SAMPLE_DOCS, Env, make_config, result_of

V1 = SAMPLE_DOCS / "versions" / "v1" / "refund_policy.docx"
V2 = SAMPLE_DOCS / "versions" / "v2" / "refund_policy.docx"

ALL_SAMPLES = [
    "employee_handbook.pdf", "supplier_code_of_conduct.docx", "store_operations_manual.md",
    "delivery_partner_faq.txt", "sales_q3.csv", "inventory.xlsx", "stores.json", "loyalty_programme.json",
]


def snapshot(env: Env):
    """Everything an upload could touch: SQLite rows and the vector store."""
    q = env.db.query
    return {
        "versions": [tuple(r) for r in q("SELECT id, document_id, version, status FROM versions ORDER BY id")],
        "documents": [tuple(r) for r in q("SELECT id, name FROM documents ORDER BY id")],
        "chunks": [tuple(r) for r in q("SELECT id, status FROM chunks ORDER BY id")],
        "tables": [tuple(r) for r in q("SELECT table_name, status FROM table_schemas ORDER BY id")],
        "vectors": env.all_vector_ids(),
        "vector_status": {v: env.vectors.status_counts(v) for (v, *_ ) in q("SELECT id FROM versions")},
        "generation": env.db.generation(),
    }


# -- AC: uploading v2 means no v1 chunk can be returned by retrieval -----------------------


def test_after_v2_no_v1_chunk_is_returned(env):
    assert result_of(env.ingest(V1)).data["version"] == 1
    r2 = result_of(env.ingest(V2))
    assert r2.event == "result" and r2.data["version"] == 2 and r2.data["status"] == "indexed"

    # Vector search over the whole store, with v1 wording, returns v2 chunks only.
    hits = env.search("Customers may return most products within 30 days restocking fee 10%", k=100)
    assert hits and {h.metadata["version"] for h in hits} == {2}
    # Keyword search for a v1-only fact finds nothing from v1.
    kw = env.keywords.search("returns@kestrel.example 30 days 6 months", k=100)
    v2_id = r2.data["version_id"]
    assert kw and {h.version_id for h in kw} == {v2_id}
    # Active chunks in SQLite are all v2; v1 chunks are kept (inactive) for rollback.
    assert {r["version"] for r in env.db.active_chunks()} == {2}
    v1_id = r2.data["version_id"] - 1
    assert env.vectors.status_counts(v1_id) == {"inactive": len(env.db.chunks_for_version(v1_id))}
    assert {r["status"] for r in env.db.chunks_for_version(v1_id)} == {"inactive"}


def test_search_filters_on_sqlite_even_if_vector_status_is_stale(env):
    """The SQLite switch is authoritative: if the vector store still says 'active' for v1
    (e.g. a crash right after the switch), v1 is still never returned."""
    result_of(env.ingest(V1))
    r2 = result_of(env.ingest(V2))
    v1_id = r2.data["version_id"] - 1
    env.vectors.set_status(v1_id, "active")  # simulate a stale mirror
    assert {h.metadata["version"] for h in env.search("return within 30 days", k=100)} == {2}


# -- AC: uploading the same file twice does not create duplicate chunks --------------------


def test_same_file_twice_is_skipped(env):
    first = result_of(env.ingest(V1))
    before = snapshot(env)
    events = env.ingest(V1)
    again = result_of(events)
    assert again.event == "result" and again.data["status"] == "duplicate"
    assert again.data["message"].startswith("Duplicate, skipped")
    assert again.data["version_id"] == first.data["version_id"]
    assert not any(e.event == "progress" for e in events)  # nothing was embedded
    assert snapshot(env) == before


def test_same_bytes_under_another_name_is_a_duplicate(env):
    result_of(env.ingest(V1))
    dup = result_of(env.ingest(V1, name="refund_policy_copy.docx"))
    assert dup.data["status"] == "duplicate" and "refund_policy.docx v1" in dup.data["message"]
    assert len(env.db.query("SELECT * FROM documents")) == 1


# -- AC: every stored chunk has doc name, version and source_location ----------------------


def test_every_chunk_is_tagged(env):
    for name in ALL_SAMPLES:
        r = result_of(env.ingest(SAMPLE_DOCS / name))
        assert r.event == "result" and r.data["status"] == "indexed", (name, r.data)
    rows = env.db.query("SELECT * FROM chunks")
    assert len(rows) > 100
    for r in rows:
        assert r["doc_name"] and r["version"] >= 1 and r["source_location"], dict(r)
        assert r["status"] == "active"
    got = env.vectors.collection.get(include=["metadatas"])
    assert len(got["ids"]) == len(rows)
    for m in got["metadatas"]:
        assert m["doc_name"] and m["version"] >= 1 and m["source_location"] and m["status"] == "active"
        assert {"page", "section"} <= set(m)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("employee_handbook.pdf", "page 2, section 4. Travel and expenses"),
        ("store_operations_manual.md", "section Cash handling, lines"),
        ("delivery_partner_faq.txt", "lines 1-"),
        ("sales_q3.csv", "row 2"),
        ("inventory.xlsx", "sheet Suppliers, row 2"),
        ("stores.json", "JSON path $[0]"),
        ("loyalty_programme.json", "JSON path $.programme.tiers"),
        ("supplier_code_of_conduct.docx", "page 1, section 4. Gifts and hospitality"),
    ],
)
def test_source_location_by_file_type(env, name, expected):
    result_of(env.ingest(SAMPLE_DOCS / name))
    locations = [r["source_location"] for r in env.db.query("SELECT source_location FROM chunks")]
    assert any(loc.startswith(expected) for loc in locations), locations


def test_pdf_chunks_carry_page_and_section(env):
    result_of(env.ingest(SAMPLE_DOCS / "employee_handbook.pdf"))
    rows = env.db.query("SELECT * FROM chunks WHERE body LIKE '%INR 2,500%'")
    assert rows and rows[0]["page"] == 2 and rows[0]["section"] == "4. Travel and expenses"
    # doc title and section heading are prepended to every chunk
    assert rows[0]["text"].startswith("Employee Handbook > 4. Travel and expenses\n")


# -- AC: a question asked while v2 is still indexing is answered from v1 -------------------


def test_v1_serves_while_v2_is_indexing(tmp_path):
    env = Env(make_config(tmp_path, ingestion={"embed_batch_size": 2}))
    try:
        result_of(env.ingest(V1))
        seen_during: list[set[int]] = []

        async def scenario():
            v2_batches = []

            async def pause(_):
                v2_batches.append(1)
                if len(v2_batches) >= 2:  # v2 is part-way through embedding, some vectors stored
                    seen_during.append({h.metadata["version"] for h in env.search("return window refund", k=100)})
                    seen_during.append({r["version"] for r in env.db.active_chunks()})

            env.embedder.before_batch = pause
            return [ev async for ev in env.ingestor.ingest(V2.name, V2.read_bytes(), "Customer Care", "Asha")]

        events = asyncio.run(scenario())
        assert result_of(events).data["version"] == 2
        assert seen_during and all(s == {1} for s in seen_during)
        assert {h.metadata["version"] for h in env.search("return window refund")} == {2}
    finally:
        env.close()


# -- AC: a failed upload leaves the existing index unchanged ------------------------------


def test_failed_embedding_leaves_index_unchanged(tmp_path):
    env = Env(make_config(tmp_path, ingestion={"embed_batch_size": 2}))
    try:
        result_of(env.ingest(V1))
        before = snapshot(env)

        v2_batches = []
        written_before_crash = []

        async def fail_on_third(_):
            v2_batches.append(1)
            if len(v2_batches) == 3:  # two v2 batches are already in the vector store
                written_before_crash.append(len(env.all_vector_ids()) - len(before["vectors"]))
                raise RuntimeError("Ollama crashed")

        env.embedder.before_batch = fail_on_third
        events = env.ingest(V2)
        assert written_before_crash == [4]
        err = result_of(events)
        assert err.event == "error" and err.data["code"] == "ingest_failed"
        assert "previous version (v1) is still active" in err.data["message"]
        assert snapshot(env) == before
        assert {r["version"] for r in env.db.active_chunks()} == {1}
        assert not list((env.ingestor.uploads_dir).glob("v2*"))
        log = env.db.one("SELECT * FROM uploads ORDER BY id DESC LIMIT 1")
        assert log["result"] == "failed"
    finally:
        env.close()


def test_failure_in_version_switch_leaves_index_unchanged(env, monkeypatch):
    result_of(env.ingest(V1))
    before = snapshot(env)
    import app.ingestion.pipeline as pipeline

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipeline, "activate_version", boom)
    assert result_of(env.ingest(V2)).event == "error"
    assert snapshot(env) == before


def test_rejected_upload_leaves_index_unchanged(env):
    result_of(env.ingest(V1))
    before = snapshot(env)
    err = result_of(env.ingest(SAMPLE_DOCS / "broken" / "scanned_notice.pdf"))
    assert err.event == "error" and err.data["code"] == "scanned_pdf"
    assert snapshot(env) == before


# -- validation (pipeline step 1) ----------------------------------------------------------


def test_scanned_pdf_rejected_with_exact_message(env):
    err = result_of(env.ingest(SAMPLE_DOCS / "broken" / "scanned_notice.pdf"))
    assert err.data == {"code": "scanned_pdf", "message": SCANNED_PDF_MESSAGE}
    assert SCANNED_PDF_MESSAGE == (
        "This PDF appears to be scanned and has no text layer. Upload a text-based PDF, or enable OCR in config (slow)."
    )
    assert env.db.one("SELECT result FROM uploads")["result"] == "rejected"


def test_empty_file_rejected(env):
    err = result_of(env.ingest(SAMPLE_DOCS / "broken" / "empty.txt"))
    assert err.event == "error" and err.data["code"] == "empty_file"


@pytest.mark.parametrize("name", ["notes.pptx", "image.png", "noextension"])
def test_unsupported_type_rejected(env, name):
    err = result_of(env.ingest(SAMPLE_DOCS / "store_operations_manual.md", name=name))
    assert err.data["code"] == "unsupported_type"


def test_file_over_size_limit_rejected(tmp_path):
    env = Env(make_config(tmp_path, ingestion={"max_file_mb": 0.001}))
    try:
        err = result_of(env.ingest(SAMPLE_DOCS / "employee_handbook.pdf"))
        assert err.data["code"] == "too_large" and "0.001 MB" in err.data["message"]
    finally:
        env.close()


def test_department_must_come_from_config(env):
    err = result_of(env.ingest(V1, department="Astronauts"))
    assert err.data["code"] == "bad_department"
    assert result_of(env.ingest(V1, owner=" ")).data["code"] == "bad_owner"


def test_corrupt_file_rejected_cleanly(env):
    err = result_of(env.ingest(V1, name="broken.docx", data=b"PK\x03\x04 not really a zip"))
    assert err.event == "error" and err.data["code"] == "unreadable"


# -- versioning record and confirmation ----------------------------------------------------


def test_version_record_and_confirmation(env):
    events = env.ingest(SAMPLE_DOCS / "employee_handbook.pdf", department="Finance", owner="Ravi")
    r = result_of(events).data
    assert r["message"] == f"Employee Handbook v1 indexed: 4 pages, {r['chunks']} chunks."
    v = env.db.one("SELECT * FROM versions")
    assert (v["department"], v["owner"], v["status"], v["version"]) == ("Finance", "Ravi", "active", 1)
    assert v["size_bytes"] == (SAMPLE_DOCS / "employee_handbook.pdf").stat().st_size
    assert v["uploaded_at"] and len(v["sha256"]) == 64
    stages = [e.data["stage"] for e in events if e.event == "stage" and e.data["status"] == "running"]
    assert stages == ["validating", "extracting", "chunking", "embedding", "storing"]
    progress = [e.data for e in events if e.event == "progress"]
    assert progress[-1]["done"] == progress[-1]["total"] == r["chunks"]
    # the original is kept for "Download the original"
    assert (env.ingestor.uploads_dir / f"v{r['version_id']}" / "employee_handbook.pdf").read_bytes() == \
        (SAMPLE_DOCS / "employee_handbook.pdf").read_bytes()


# -- table path ------------------------------------------------------------------------------


def test_csv_loaded_into_sqlite_with_original_columns(env):
    r = result_of(env.ingest(SAMPLE_DOCS / "sales_q3.csv", department="Finance")).data
    assert r["message"] == "Sales Q3 v1 indexed: 1 table, 60 rows, 60 chunks."
    [schema] = env.db.active_table_schemas()
    assert [c["name"] for c in schema["columns"]] == ["Product", "Region", "Month", "Units", "Sales"]
    assert [c["type"] for c in schema["columns"]] == ["TEXT", "TEXT", "TEXT", "INTEGER", "INTEGER"]
    assert schema["row_count"] == 60 and schema["file"] == "sales_q3.csv" and schema["sheet"] is None
    total = env.db.one(f'SELECT SUM("Sales") AS s, COUNT(*) AS n FROM "{schema["table_name"]}"')
    import csv
    with (SAMPLE_DOCS / "sales_q3.csv").open() as fh:
        expected = sum(int(row["Sales"]) for row in csv.DictReader(fh))
    assert (total["s"], total["n"]) == (expected, 60)
    # each row is also a sentence chunk
    first = env.db.one("SELECT * FROM chunks ORDER BY ord LIMIT 1")
    assert first["body"].startswith("Product: Shirt, Region: North, Month: 2026-07, Units: ")
    assert first["source_location"] == "row 2"


def test_multi_sheet_excel_one_table_per_sheet_same_version(env):
    r = result_of(env.ingest(SAMPLE_DOCS / "inventory.xlsx", department="Merchandising")).data
    schemas = env.db.active_table_schemas()
    assert [s["sheet"] for s in schemas] == ["Stock", "Suppliers"]
    assert {s["version_id"] for s in schemas} == {r["version_id"]}
    assert [c["type"] for c in schemas[1]["columns"]] == ["TEXT", "TEXT", "INTEGER", "REAL"]


def test_json_records_become_a_table_and_nested_json_becomes_text(env):
    result_of(env.ingest(SAMPLE_DOCS / "stores.json", department="Retail Operations"))
    [schema] = env.db.active_table_schemas()
    assert [c["name"] for c in schema["columns"]][:3] == ["store_id", "city", "region"]
    assert schema["row_count"] == 8
    result_of(env.ingest(SAMPLE_DOCS / "loyalty_programme.json", department="Marketing"))
    assert len(env.db.active_table_schemas()) == 1  # nested JSON added no table
    bodies = " ".join(r["body"] for r in env.db.query("SELECT body FROM chunks WHERE doc_name = 'loyalty_programme.json'"))
    assert "programme.tiers.gold.min_annual_spend: 25000" in bodies


def test_new_table_version_deactivates_old_table(env):
    path = SAMPLE_DOCS / "sales_q3.csv"
    result_of(env.ingest(path, department="Finance"))
    changed = path.read_bytes() + b"Shirt,North,2026-10,1,899\r\n"
    r2 = result_of(env.ingest(path, data=changed, department="Finance")).data
    assert r2["version"] == 2
    active = env.db.active_table_schemas()
    assert len(active) == 1 and active[0]["version_id"] == r2["version_id"] and active[0]["row_count"] == 61
    assert env.db.one("SELECT status FROM table_schemas WHERE version_id = ?", (r2["version_id"] - 1,))["status"] == \
        "inactive"


def test_failed_first_upload_says_nothing_was_stored(env):
    async def fail(_):
        raise RuntimeError("Ollama crashed")

    env.embedder.before_batch = fail
    err = result_of(env.ingest(V1))
    assert err.data["message"] == "Indexing failed: Ollama crashed. Nothing was stored."
    assert env.db.query("SELECT * FROM documents") == [] and env.all_vector_ids() == []
