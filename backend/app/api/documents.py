"""Document endpoints (spec section 8): upload with streamed steps, and the list."""

from __future__ import annotations

import time

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse

from ..schemas import DocumentList, DocumentSummary
from .sse import SSE_HEADERS, sse

router = APIRouter(prefix="/api")

_STREAM_DOC = {
    200: {
        "description": "text/event-stream: `stage`, `progress`, then `result` or `error`, and always `done` last. "
        "Payloads: StageEvent, ProgressEvent, UploadResult, ErrorEvent, DoneEvent.",
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
    }
}


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
    db = request.app.state.db
    rows = db.query(
        "SELECT d.id, d.name, d.file_type, v.title, v.path, v.department, v.owner, v.version AS active_version, "
        "(SELECT COUNT(*) FROM versions x WHERE x.document_id = d.id AND x.status != 'pending') AS version_count, "
        "v.pages, v.sections, v.chunk_count AS chunks, v.table_rows, v.size_bytes, "
        "COALESCE(v.activated_at, v.uploaded_at) AS updated_at "
        "FROM documents d JOIN versions v ON v.document_id = d.id AND v.status = 'active' "
        "ORDER BY updated_at DESC"
    )
    return DocumentList(documents=[DocumentSummary(**dict(r)) for r in rows])
