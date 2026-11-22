"""Versioning and the atomic switch (spec section 4).

A new version is indexed with status pending while the old one stays active. Only after
indexing succeeds, ONE SQLite transaction marks the new version active, the old one
inactive, and all the old version's chunks and tables inactive. Inactive content stays
stored (rollback, document list) but every search excludes it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import Config
from ..store.sqlite import Database, now_iso
from ..store.vectors import VectorStore
from .tables import drop_tables


class EmbeddingModelMismatch(RuntimeError):
    pass


def activate_version(db: Database, vectors: VectorStore, config: Config, version_id: int) -> int | None:
    """Make version_id the active version of its document. Returns the previously active id."""
    with db.transaction() as conn:
        row = conn.execute("SELECT document_id FROM versions WHERE id = ?", (version_id,)).fetchone()
        if row is None:
            raise ValueError(f"version {version_id} does not exist")
        doc_id = row["document_id"]
        old = conn.execute(
            "SELECT id FROM versions WHERE document_id = ? AND status = 'active' AND id != ?", (doc_id, version_id)
        ).fetchone()
        old_id = old["id"] if old else None
        if old_id is not None:
            conn.execute("UPDATE versions SET status = 'inactive' WHERE id = ?", (old_id,))
            conn.execute("UPDATE chunks SET status = 'inactive' WHERE version_id = ?", (old_id,))
            conn.execute("UPDATE table_schemas SET status = 'inactive' WHERE version_id = ?", (old_id,))
        conn.execute(
            "UPDATE versions SET status = 'active', activated_at = ?, embedding_model = ? WHERE id = ?",
            (now_iso(), config.models.embedding, version_id),
        )
        conn.execute("UPDATE chunks SET status = 'active' WHERE version_id = ?", (version_id,))
        conn.execute("UPDATE table_schemas SET status = 'active' WHERE version_id = ?", (version_id,))
        if db.get_meta("embedding_model") is None:
            db.set_meta("embedding_model", config.models.embedding, conn)
        db.bump_generation(conn)
    # Mirror into the vector store. Searches also filter on SQLite's active ids, so the
    # switch is already effective even if this step is interrupted; reconcile() repairs it.
    vectors.set_status(version_id, "active")
    if old_id is not None:
        vectors.set_status(old_id, "inactive")
    return old_id


def discard_version(db: Database, vectors: VectorStore, version_id: int) -> None:
    """Remove a pending (failed or abandoned) version: vectors, chunks, tables, file, row."""
    row = db.one("SELECT * FROM versions WHERE id = ?", (version_id,))
    if row is None:
        return
    if row["status"] == "active":
        raise ValueError("refusing to discard the active version")
    try:
        vectors.delete_version(version_id)
    except Exception:
        pass  # reconcile() removes orphaned vectors later
    with db.transaction() as conn:
        drop_tables(conn, version_id)
        conn.execute("DELETE FROM chunks WHERE version_id = ?", (version_id,))
        conn.execute("DELETE FROM versions WHERE id = ?", (version_id,))
        left = conn.execute("SELECT COUNT(*) AS n FROM versions WHERE document_id = ?", (row["document_id"],)).fetchone()
        if left["n"] == 0:
            conn.execute("DELETE FROM documents WHERE id = ?", (row["document_id"],))
    folder = Path(row["original_path"]).parent
    shutil.rmtree(folder, ignore_errors=True)


def check_embedding_model(db: Database, config: Config) -> None:
    """The backend refuses to start if the index was built with another embedding model."""
    stored = db.get_meta("embedding_model")
    if stored is not None and stored != config.models.embedding:
        raise EmbeddingModelMismatch(
            f"The index was built with embedding model '{stored}', but config.yaml says "
            f"'{config.models.embedding}'. Run: cd backend; .venv\\Scripts\\python -m app.reindex"
        )


def reconcile(db: Database, vectors: VectorStore) -> dict[str, int]:
    """Start-up repair after a crash: drop leftover pending versions and make the vector
    store's status match SQLite for every version."""
    pending = [r["id"] for r in db.query("SELECT id FROM versions WHERE status = 'pending'")]
    for vid in pending:
        discard_version(db, vectors, vid)
    fixed = 0
    for r in db.query("SELECT id, status FROM versions"):
        counts = vectors.status_counts(r["id"])
        if counts and set(counts) != {r["status"]}:
            vectors.set_status(r["id"], r["status"])
            fixed += 1
    return {"discarded_pending": len(pending), "status_fixed": fixed}
