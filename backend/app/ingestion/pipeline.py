"""The upload pipeline (spec section 4), as an async stream of events (spec section 8.1).

validating -> extracting -> chunking -> embedding (done/total) -> storing, then `result`.
Uploads run one at a time. On any failure the new version is discarded and the
previous version stays active and untouched.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, AsyncIterator, Protocol

from ..config import Config
from ..store.sqlite import Database, now_iso
from ..store.vectors import VectorStore, chunk_metadata
from .chunk import Chunk, chunk_document
from .errors import IngestError
from .extract import TableDoc, TextDoc, extract
from .tables import create_tables
from .validate import clean_filename, validate
from .versioning import activate_version, discard_version


class Embedder(Protocol):
    async def embed_documents(self, chunks: list[str]) -> list[list[float]]: ...


@dataclass
class Event:
    event: str  # stage | progress | result | error
    data: dict[str, Any]


@dataclass
class _Run:
    filename: str
    department: str
    owner: str
    size: int
    events: list[Event] = field(default_factory=list)
    version_id: int | None = None


def summary_line(title: str, version: int, doc: TextDoc | TableDoc, n_chunks: int) -> str:
    if isinstance(doc, TableDoc):
        rows = sum(len(t.rows) for t in doc.tables)
        tables = f"{len(doc.tables)} table" + ("s" if len(doc.tables) != 1 else "")
        return f"{title} v{version} indexed: {tables}, {rows} rows, {n_chunks} chunks."
    if doc.pages:
        return f"{title} v{version} indexed: {doc.pages} page{'s' if doc.pages != 1 else ''}, {n_chunks} chunks."
    n = len(doc.sections)
    return f"{title} v{version} indexed: {n} section{'s' if n != 1 else ''}, {n_chunks} chunks."


class Ingestor:
    def __init__(self, config: Config, db: Database, vectors: VectorStore, embedder: Embedder):
        self.config = config
        self.db = db
        self.vectors = vectors
        self.embedder = embedder
        self._lock = asyncio.Lock()

    @property
    def uploads_dir(self):
        return self.config.sqlite_path.parent / "uploads"

    async def ingest(self, filename: str, data: bytes, department: str, owner: str) -> AsyncIterator[Event]:
        """Run the pipeline; yields events. Exactly one `result` or `error` event is yielded."""
        async with self._lock:
            run = _Run(clean_filename(filename), (department or "").strip(), (owner or "").strip(), len(data))
            outcome, message = "failed", ""
            try:
                async for ev in self._pipeline(run, data):
                    run.events.append(ev)
                    if ev.event == "result":
                        outcome, message = ev.data["status"], ev.data["message"]
                    yield ev
            except IngestError as exc:
                outcome = "rejected" if run.version_id is None else "failed"
                message = exc.message
                ev = Event("error", {"code": exc.code, "message": exc.message})
                run.events.append(ev)
                yield ev
            except Exception as exc:
                outcome = "failed"
                prev = self.db.one(
                    "SELECT v.version FROM versions v JOIN documents d ON d.id = v.document_id "
                    "WHERE d.name = ? AND v.status = 'active'", (run.filename,))
                message = f"Indexing failed: {str(exc).rstrip('.')}." + (
                    f" The previous version (v{prev['version']}) is still active." if prev else " Nothing was stored.")
                ev = Event("error", {"code": "ingest_failed", "message": message})
                run.events.append(ev)
                yield ev
            finally:
                if outcome != "indexed" and run.version_id is not None:
                    discard_version(self.db, self.vectors, run.version_id)
                    run.version_id = None
                self.db.log_upload(
                    filename=run.filename or filename or "",
                    department=run.department,
                    owner=run.owner,
                    size_bytes=run.size,
                    result=outcome,
                    message=message,
                    version_id=run.version_id,
                    steps=[{"event": e.event, "data": e.data} for e in run.events],
                )

    async def _pipeline(self, run: _Run, data: bytes) -> AsyncIterator[Event]:
        cfg = self.config
        t0 = time.perf_counter()

        yield _stage("validating", "Checking the file")
        if run.department not in cfg.ui.departments:
            raise IngestError("bad_department", f"Department must be one of: {', '.join(cfg.ui.departments)}.")
        if not run.owner:
            raise IngestError("bad_owner", "Owner is required.")
        kind = validate(run.filename, data, cfg)
        sha = hashlib.sha256(data).hexdigest()
        dup = self.db.find_by_sha(sha)
        if dup is not None:
            yield _stage("validating", "Duplicate file", "done")
            yield Event("result", {
                "status": "duplicate",
                "message": f"Duplicate, skipped: this file is identical to {dup['doc_name']} v{dup['version']}.",
                "document_id": dup["document_id"], "version_id": dup["id"], "version": dup["version"],
                "doc_name": dup["doc_name"], "pages": dup["pages"], "sections": dup["sections"],
                "chunks": dup["chunk_count"], "table_rows": dup["table_rows"],
                "elapsed_ms": _ms(t0),
            })
            return
        yield _stage("validating", f"{kind.upper()}, {len(data) / 1024:.0f} KB, not seen before", "done")

        yield _stage("extracting", "Reading the text")
        doc = await asyncio.to_thread(extract, kind, run.filename, data, cfg)
        if isinstance(doc, TableDoc):
            detail = f"{len(doc.tables)} table(s): " + ", ".join(
                f"{t.sheet or PurePath(run.filename).stem} ({len(t.rows)} rows)" for t in doc.tables)
        else:
            detail = (f"{doc.pages} pages, " if doc.pages else "") + f"{len(doc.sections)} sections found"
        yield _stage("extracting", detail, "done", sections=_section_names(doc))

        yield _stage("chunking", "Splitting into chunks")
        chunks = await asyncio.to_thread(chunk_document, doc, cfg)
        if not chunks:
            raise IngestError("no_text", f"No text was found in {run.filename}.")
        yield _stage("chunking", f"{len(chunks)} chunks", "done", chunk_map=_chunk_map(chunks))

        version_id, version = self._create_pending(run, kind, sha, doc, chunks)
        run.version_id = version_id
        self._save_original(version_id, run.filename, data)

        yield _stage("embedding", "Making it searchable")
        rows = self.db.chunks_for_version(version_id)
        total, size = len(rows), cfg.ingestion.embed_batch_size
        yield Event("progress", {"stage": "embedding", "done": 0, "total": total})
        for i in range(0, total, size):
            batch = rows[i : i + size]
            vecs = await self.embedder.embed_documents([r["text"] for r in batch])
            self.vectors.add([r["id"] for r in batch], vecs, [chunk_metadata(r) for r in batch], [r["text"] for r in batch])
            yield Event("progress", {"stage": "embedding", "done": min(i + size, total), "total": total})
        yield _stage("embedding", f"{total} of {total} chunks embedded", "done")

        yield _stage("storing", "Switching versions")
        old = activate_version(self.db, self.vectors, cfg, version_id)
        old_v = self.db.one("SELECT version FROM versions WHERE id = ?", (old,)) if old else None
        yield _stage("storing", f"v{version} active" + (f", v{old_v['version']} inactive" if old_v else ""), "done")

        row = self.db.one("SELECT * FROM versions WHERE id = ?", (version_id,))
        yield Event("result", {
            "status": "indexed",
            "message": summary_line(doc.title, version, doc, len(chunks)),
            "document_id": row["document_id"], "version_id": version_id, "version": version,
            "doc_name": run.filename, "pages": row["pages"], "sections": row["sections"],
            "chunks": row["chunk_count"], "table_rows": row["table_rows"],
            "elapsed_ms": _ms(t0),
        })

    def _create_pending(self, run: _Run, kind: str, sha: str, doc, chunks: list[Chunk]) -> tuple[int, int]:
        is_table = isinstance(doc, TableDoc)
        with self.db.transaction() as conn:
            d = conn.execute("SELECT id FROM documents WHERE name = ?", (run.filename,)).fetchone()
            if d is None:
                cur = conn.execute(
                    "INSERT INTO documents(name, file_type, created_at) VALUES (?, ?, ?)", (run.filename, kind, now_iso())
                )
                doc_id = int(cur.lastrowid)
            else:
                doc_id = int(d["id"])
            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM versions WHERE document_id = ?", (doc_id,)
            ).fetchone()["v"]
            sections = 0 if is_table else len(doc.sections)
            rows = sum(len(t.rows) for t in doc.tables) if is_table else 0
            cur = conn.execute(
                "INSERT INTO versions(document_id, version, filename, title, sha256, size_bytes, department, owner, "
                "uploaded_at, status, path, pages, sections, chunk_count, table_rows, original_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, '')",
                (doc_id, version, run.filename, doc.title, sha, run.size, run.department, run.owner, now_iso(),
                 "table" if is_table else "text", None if is_table else doc.pages, sections, len(chunks), rows),
            )
            vid = int(cur.lastrowid)
            path = self.uploads_dir / f"v{vid}" / run.filename
            conn.execute("UPDATE versions SET original_path = ? WHERE id = ?", (str(path), vid))
            conn.executemany(
                "INSERT INTO chunks(id, version_id, document_id, ord, doc_name, version, page, section, "
                "source_location, text, body, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                [(f"v{vid}-c{i:05d}", vid, doc_id, i, run.filename, version, c.page, c.section, c.source_location,
                  c.text, c.body) for i, c in enumerate(chunks)],
            )
            if is_table:
                create_tables(conn, doc, version_id=vid, document_id=doc_id, filename=run.filename,
                              stem=PurePath(run.filename).stem)
        return vid, int(version)

    def _save_original(self, version_id: int, filename: str, data: bytes) -> None:
        folder = self.uploads_dir / f"v{version_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_bytes(data)

    async def rebuild_version(self, version_id: int) -> dict[str, Any]:
        """Re-index one version from its stored original with the current chunk settings and
        embedding model. The new chunks are built and embedded first; one transaction then
        swaps them in, so searches never see a half-rebuilt document."""
        async with self._lock:
            row = self.db.one("SELECT * FROM versions WHERE id = ?", (version_id,))
            if row is None:
                raise IngestError("not_found", f"Version {version_id} does not exist.")
            data = Path(row["original_path"]).read_bytes()
            kind = validate(row["filename"], data, self.config)
            doc = await asyncio.to_thread(extract, kind, row["filename"], data, self.config)
            chunks = await asyncio.to_thread(chunk_document, doc, self.config)
            gen = self.db.generation() + 1
            status = row["status"]
            new_rows = [(f"v{version_id}-g{gen}-c{i:05d}", version_id, row["document_id"], i, row["filename"],
                         row["version"], c.page, c.section, c.source_location, c.text, c.body) for i, c in
                        enumerate(chunks)]
            size = self.config.ingestion.embed_batch_size
            meta_rows = [dict(zip(("id", "version_id", "document_id", "ord", "doc_name", "version", "page", "section",
                                   "source_location", "text", "body"), r)) | {"status": "pending"} for r in new_rows]
            try:
                for i in range(0, len(meta_rows), size):
                    batch = meta_rows[i : i + size]
                    vecs = await self.embedder.embed_documents([r["text"] for r in batch])
                    self.vectors.add([r["id"] for r in batch], vecs, [chunk_metadata(r) for r in batch],
                                     [r["text"] for r in batch])
            except BaseException:
                self.vectors.collection.delete(ids=[r["id"] for r in meta_rows])
                raise
            old_ids = [r["id"] for r in self.db.chunks_for_version(version_id)]
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM chunks WHERE version_id = ?", (version_id,))
                conn.executemany(
                    "INSERT INTO chunks(id, version_id, document_id, ord, doc_name, version, page, section, "
                    "source_location, text, body, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [r + (status,) for r in new_rows])
                conn.execute("UPDATE versions SET chunk_count = ?, embedding_model = ?, title = ? WHERE id = ?",
                             (len(chunks), self.config.models.embedding, doc.title, version_id))
                self.db.bump_generation(conn)
            if old_ids:
                self.vectors.collection.delete(ids=old_ids)
            self.vectors.set_status(version_id, status)
            return {"version_id": version_id, "chunks": len(chunks)}

    async def rollback(self, document_id: int, version: int) -> dict[str, Any]:
        """Make an older version active again (atomic, like an upload). Its chunks are
        re-embedded first if they were embedded with another model or dropped by a re-index."""
        async with self._lock:
            row = self.db.one("SELECT * FROM versions WHERE document_id = ? AND version = ?", (document_id, version))
            if row is None:
                raise IngestError("not_found", f"Version {version} of document {document_id} does not exist.")
            if row["status"] == "active":
                raise IngestError("already_active", f"v{version} is already the active version.")
            chunks = self.db.chunks_for_version(row["id"])
            stored = len(self.vectors.ids_for_version(row["id"]))
            if row["embedding_model"] != self.config.models.embedding or stored != len(chunks):
                size = self.config.ingestion.embed_batch_size
                for i in range(0, len(chunks), size):
                    batch = chunks[i : i + size]
                    vecs = await self.embedder.embed_documents([r["text"] for r in batch])
                    self.vectors.add([r["id"] for r in batch], vecs, [chunk_metadata(r) for r in batch],
                                     [r["text"] for r in batch])
            old = activate_version(self.db, self.vectors, self.config, row["id"])
            return {"document_id": document_id, "active_version_id": row["id"], "previous_version_id": old}


def _stage(stage: str, detail: str, status: str = "running", **extra: Any) -> Event:
    data: dict[str, Any] = {"stage": stage, "detail": detail, "status": status}
    if extra:
        data["data"] = extra
    return Event("stage", data)


def _section_names(doc) -> list[str]:
    if isinstance(doc, TableDoc):
        return [t.sheet or "rows" for t in doc.tables]
    return doc.sections


def _chunk_map(chunks: list[Chunk]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in chunks:
        name = c.section or "(no section)"
        if out and out[-1]["section"] == name:
            out[-1]["chunks"] += 1
        else:
            out.append({"section": name, "chunks": 1})
    return out


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
