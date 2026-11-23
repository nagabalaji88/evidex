"""POST /api/ask (streams steps, then the answer) and GET /api/traces/{id}."""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..schemas import AskRequest, TraceResponse
from .sse import SSE_HEADERS, sse

router = APIRouter(prefix="/api")

_STREAM_DOC = {200: {"description": "text/event-stream: `stage` events (searching -> ranking -> writing -> checking; "
                                    "finished steps carry data.step), then `answer` or `error`, and always `done` last.",
                     "content": {"text/event-stream": {"schema": {"type": "string"}}}}}


@router.post("/ask", responses=_STREAM_DOC, response_class=StreamingResponse)
async def ask(body: AskRequest, request: Request):
    service = request.app.state.ask_service

    async def stream():
        t0 = time.perf_counter()
        try:
            async for ev in service.ask(body.question, session_id=body.session_id, document_ids=body.document_ids):
                yield sse(ev.event, ev.data)
        except Exception as exc:  # never hang the client
            yield sse("error", {"code": "internal_error", "message": f"Something went wrong: {exc}"})
        finally:
            yield sse("done", {"elapsed_ms": int((time.perf_counter() - t0) * 1000)})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/traces/{trace_id}", response_model=TraceResponse)
async def get_trace(trace_id: int, request: Request) -> TraceResponse:
    row = request.app.state.db.one("SELECT * FROM question_log WHERE id = ?", (trace_id,))
    if row is None:
        raise HTTPException(404, "No such trace")
    keys = row.keys()
    answer = json.loads(row["answer_json"]) if "answer_json" in keys and row["answer_json"] else None
    if answer is not None:
        answer["trace_id"] = trace_id
    return TraceResponse(id=row["id"], created_at=row["created_at"], question=row["question"],
                         standalone_question=row["standalone_question"], route=row["route"], outcome=row["outcome"],
                         model=row["model"] if "model" in keys else None, steps=json.loads(row["steps"]),
                         answer=answer, total_ms=row["total_ms"] if "total_ms" in keys else None)
