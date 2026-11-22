"""Re-index command, start-up embedding-model check, rollback and crash recovery (spec section 4)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.ingestion.versioning import EmbeddingModelMismatch, reconcile
from app.main import create_app
from app.reindex import reindex

from .conftest import SAMPLE_DOCS, Env, FakeEmbedder, FakeOllama, FakeReranker, make_config, result_of

V1 = SAMPLE_DOCS / "versions" / "v1" / "refund_policy.docx"
V2 = SAMPLE_DOCS / "versions" / "v2" / "refund_policy.docx"


def app_for(cfg, embedder=None):
    return create_app(cfg, ollama_transport=FakeOllama().transport, reranker_loader=FakeReranker,
                      embedder=embedder or FakeEmbedder())


def test_backend_refuses_to_start_when_embedding_model_changed(tmp_path):
    cfg = make_config(tmp_path)
    env = Env(cfg)
    result_of(env.ingest(V1))
    assert env.db.get_meta("embedding_model") == "nomic-embed-text"
    env.close()

    changed = make_config(tmp_path, models={"embedding": "bge-m3"})
    with pytest.raises(EmbeddingModelMismatch, match="python -m app.reindex"):
        with TestClient(app_for(changed)):
            pass
    with TestClient(app_for(cfg)) as c:  # the original model still starts
        assert c.get("/api/health").status_code == 200


def test_reindex_reembeds_active_chunks_with_new_model_then_backend_starts(tmp_path):
    cfg = make_config(tmp_path)
    env = Env(cfg)
    result_of(env.ingest(V1))
    result_of(env.ingest(V2))
    result_of(env.ingest(SAMPLE_DOCS / "sales_q3.csv", department="Finance"))
    active = env.db.active_chunks()
    env.close()

    new_cfg = make_config(tmp_path, models={"embedding": "bge-m3"})
    env = Env(new_cfg)
    embedder = FakeEmbedder()
    out = asyncio.run(reindex(new_cfg, env.db, env.vectors, embedder, log=lambda *_: None))
    assert out["chunks"] == len(active)
    assert sorted(embedder.texts) == sorted(r["text"] for r in active)
    assert env.all_vector_ids() == sorted(r["id"] for r in active)  # store rebuilt from active chunks only
    assert env.db.get_meta("embedding_model") == "bge-m3"
    models = {r["status"]: r["embedding_model"] for r in env.db.query("SELECT status, embedding_model FROM versions")}
    assert models == {"active": "bge-m3", "inactive": None}
    assert {h.metadata["version"] for h in env.search("return window", k=100) if h.metadata["doc_name"] ==
            "refund_policy.docx"} == {2}
    env.close()
    with TestClient(app_for(new_cfg)) as c:
        assert c.get("/api/health").status_code == 200


def test_rollback_is_atomic_and_reembeds_after_reindex(tmp_path):
    cfg = make_config(tmp_path)
    env = Env(cfg)
    r1 = result_of(env.ingest(V1)).data
    result_of(env.ingest(V2))
    asyncio.run(reindex(cfg, env.db, env.vectors, FakeEmbedder(), log=lambda *_: None))
    assert env.vectors.ids_for_version(r1["version_id"]) == []  # v1 lost its vectors

    calls_before = env.embedder.calls
    asyncio.run(env.ingestor.rollback(r1["document_id"], 1))
    assert env.embedder.calls > calls_before  # v1 re-embedded before activation
    assert {r["version"] for r in env.db.active_chunks()} == {1}
    assert {h.metadata["version"] for h in env.search("return window", k=100)} == {1}

    # rolling forward again needs no re-embedding: v2 still has current-model vectors
    calls_before = env.embedder.calls
    asyncio.run(env.ingestor.rollback(r1["document_id"], 2))
    assert env.embedder.calls == calls_before
    assert {h.metadata["version"] for h in env.search("return window", k=100)} == {2}
    env.close()


def test_startup_discards_leftover_pending_version(env):
    result_of(env.ingest(V1))
    good = env.all_vector_ids()

    async def crash_mid_upload():
        gen = env.ingestor._pipeline(
            __import__("app.ingestion.pipeline", fromlist=["_Run"])._Run(V2.name, "Customer Care", "Asha", 1),
            V2.read_bytes(),
        )
        async for ev in gen:
            if ev.event == "progress" and ev.data["done"] > 0:
                break  # process "dies" with v2 pending and partly embedded
        await gen.aclose()

    asyncio.run(crash_mid_upload())
    assert env.db.one("SELECT COUNT(*) AS n FROM versions WHERE status = 'pending'")["n"] == 1
    out = reconcile(env.db, env.vectors)
    assert out["discarded_pending"] == 1
    assert env.all_vector_ids() == good
    assert env.db.one("SELECT COUNT(*) AS n FROM versions")["n"] == 1


def test_startup_repairs_stale_vector_status(env):
    result_of(env.ingest(V1))
    r2 = result_of(env.ingest(V2)).data
    env.vectors.set_status(r2["version_id"] - 1, "active")  # crash between SQLite switch and mirror
    assert reconcile(env.db, env.vectors)["status_fixed"] == 1
    assert env.vectors.status_counts(r2["version_id"] - 1).keys() == {"inactive"}
