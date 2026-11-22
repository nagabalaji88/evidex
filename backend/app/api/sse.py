"""Server-Sent Events framing (spec section 8.1). Read in the browser with fetch + a
stream reader, never EventSource (it cannot POST)."""

from __future__ import annotations

from pydantic import BaseModel

from ..schemas import AnswerPayload, DoneEvent, ErrorEvent, ProgressEvent, StageEvent, UploadResult

EVENT_MODELS: dict[str, type[BaseModel]] = {
    "stage": StageEvent,
    "progress": ProgressEvent,
    "result": UploadResult,
    "answer": AnswerPayload,
    "error": ErrorEvent,
    "done": DoneEvent,
}

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def sse(event: str, payload: dict | BaseModel) -> str:
    """One SSE frame; the payload is validated against its Pydantic model first."""
    model = EVENT_MODELS[event]
    obj = payload if isinstance(payload, model) else model.model_validate(payload)
    return f"event: {event}\ndata: {obj.model_dump_json()}\n\n"
