"""Document endpoints (spec section 8): upload with streamed steps, list, detail, rollback,
re-index, download the original, and the recent uploads table."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..ingestion.errors import IngestError
from ..schemas import (ActionResult, DocumentDetail, DocumentList, DocumentSummary, RollbackRequest, SectionInfo,
                       TableInfo, UploadList, UploadRow, UploadSteps, VersionInfo)
from ..store.sqlite import quote_ident
from ..text import estimate_tokens
from .sse import SSE_HEADERS, sse

router = APIRouter(prefix="/api")

_STREAM_DOC = {
    200: {
        "description": "text/event-stream: `stage`, `progress`, then `result` or `error`, and always `done` last. "
        "Payloads: StageEvent, ProgressEvent, UploadResult, ErrorEvent, DoneEvent.",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}

_SUMMARY_SQL = (
    "SELECT d.id, d.name, d.file_type, v.title, v.path, v.department, v.owner, v.version AS active_version, "
    "(SELECT COUNT(*) FROM versions x WHERE x.document_id = d.id AND x.status != 'pending') AS version_count, "
    "v.pages, v.sections, v.chunk_count AS chunks, v.table_rows, v.size_bytes, "
    "COALESCE(v.activated_at, v.uploaded_at) AS updated_at "
    "FROM documents d JOIN versions v ON v.document_id = d.id AND v.status = 'active' "
)


@router.post("/documents", responses=_STREAM_DOC, response_class=StreamingResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    department: str = Form(""),
    owner: str = Form(""),
):
    cfg = request.app.state.config
    # Read at most one byte past the limit; validation rejects anything larger.
    data = await file.read(int(cfg.ingestion.max_file_mb * 1024 * 1024) + 1)
    ingestor = request.app.state.ingestor

    async def stream():
        t0 = time.perf_counter()
        try:
            async for ev in ingestor.ingest(file.filename or "", data, department, owner):
                yield sse(ev.event, ev.data)
        finally:
            yield sse("done", {"elapsed_ms": int((time.perf_counter() - t0) * 1000)})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/documents", response_model=DocumentList)
async def list_documents(request: Request) -> DocumentList:
    rows = request.app.state.db.query(_SUMMARY_SQL + "ORDER BY updated_at DESC")
    return DocumentList(documents=[DocumentSummary(**dict(r)) for r in rows])


def _used_in_answers(db, name: str) -> int:
    row = db.one("SELECT COUNT(*) AS n FROM question_log WHERE cited_documents LIKE ?",
                 (f"%{json.dumps(name)}%",))
    return int(row["n"]) if row else 0


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: int, request: Request) -> DocumentDetail:
    db = request.app.state.db
    row = db.one(_SUMMARY_SQL + "WHERE d.id = ?", (document_id,))
    if row is None:
        raise HTTPException(404, "No such document, or it has no active version.")
    summary = DocumentSummary(**dict(row))
    versions = []
    for v in db.query("SELECT * FROM versions WHERE document_id = ? AND status != 'pending' ORDER BY version",
                      (document_id,)):
        note = "First upload" if v["version"] == 1 else f"New version uploaded by {v['owner']}"
        versions.append(VersionInfo(id=v["id"], version=v["version"], status=v["status"], uploaded_at=v["uploaded_at"],
                                    department=v["department"], owner=v["owner"], size_bytes=v["size_bytes"],
                                    pages=v["pages"], sections=v["sections"], chunks=v["chunk_count"],
                                    table_rows=v["table_rows"], note=note))
    active = db.active_version(document_id)
    sections: list[SectionInfo] = []
    tables: list[TableInfo] = []
    if summary.path == "table":
        for s in db.table_schemas(active["id"]):
            sample = [list(r) for r in db.query(f"SELECT * FROM {quote_ident(s['table_name'])} LIMIT 3")]
            tables.append(TableInfo(table_name=s["table_name"], sheet=s["sheet"], columns=s["columns"],
                                    row_count=s["row_count"], sample_rows=sample))
    else:
        for c in db.chunks_for_version(active["id"]):
            name = c["section"] or "(no section)"
            if sections and sections[-1].name == name:
                sections[-1].chunks += 1
                continue
            sections.append(SectionInfo(name=name, page=c["page"], chunks=1, source_location=c["source_location"],
                                        preview=c["body"][:400], tokens=estimate_tokens(c["text"])))
    return DocumentDetail(summary=summary, versions=versions, sections=sections, tables=tables,
                          used_in_answers=_used_in_answers(db, summary.name))


@router.post("/documents/{document_id}/rollback", response_model=ActionResult)
async def rollback(document_id: int, body: RollbackRequest, request: Request) -> ActionResult:
    try:
        await request.app.state.ingestor.rollback(document_id, body.version)
    except IngestError as exc:
        raise HTTPException(400, exc.message) from exc
    return ActionResult(ok=True, message=f"v{body.version} is active again. Questions now search v{body.version} only.")


@router.post("/documents/{document_id}/reindex", response_model=ActionResult)
async def reindex_document(document_id: int, request: Request) -> ActionResult:
    db = request.app.state.db
    active = db.active_version(document_id)
    if active is None:
        raise HTTPException(404, "No such document, or it has no active version.")
    try:
        out = await request.app.state.ingestor.rebuild_version(active["id"])
    except IngestError as exc:
        raise HTTPException(400, exc.message) from exc
    except Exception as exc:
        raise HTTPException(502, f"Re-index failed: {exc}. The previous index is unchanged.") from exc
    return ActionResult(ok=True, message=f"{active['filename']} v{active['version']} re-indexed: {out['chunks']} chunks.")


@router.get("/documents/{document_id}/download")
async def download(document_id: int, request: Request, version: int | None = None):
    db = request.app.state.db
    row = db.one("SELECT * FROM versions WHERE document_id = ? AND " +
                 ("version = ?" if version is not None else "status = 'active'"),
                 (document_id, version) if version is not None else (document_id,))
    if row is None or not Path(row["original_path"]).is_file():
        raise HTTPException(404, "The original file is not available.")
    return FileResponse(row["original_path"], filename=row["filename"])


@router.get("/uploads", response_model=UploadList)
async def list_uploads(request: Request, limit: int = 20) -> UploadList:
    db = request.app.state.db
    rows = db.query("SELECT u.*, v.version AS version, v.status AS vstatus FROM uploads u "
                    "LEFT JOIN versions v ON v.id = u.version_id ORDER BY u.id DESC LIMIT ?", (limit,))
    out = []
    for r in rows:
        status = {"duplicate": "skipped", "rejected": "rejected", "failed": "failed"}.get(r["result"]) or \
            (r["vstatus"] or "inactive")
        out.append(UploadRow(id=r["id"], filename=r["filename"], file_type=Path(r["filename"]).suffix.lstrip(".").upper()
                             or "?", version=r["version"], department=r["department"], owner=r["owner"],
                             uploaded_at=r["uploaded_at"], size_bytes=r["size_bytes"], result=r["result"],
                             status=status, message=r["message"]))
    return UploadList(uploads=out)


@router.get("/uploads/{upload_id}", response_model=UploadSteps)
async def upload_steps(upload_id: int, request: Request) -> UploadSteps:
    r = request.app.state.db.one("SELECT * FROM uploads WHERE id = ?", (upload_id,))
    if r is None:
        raise HTTPException(404, "No such upload")
    return UploadSteps(id=r["id"], filename=r["filename"], result=r["result"], message=r["message"],
                       events=json.loads(r["steps"]))
