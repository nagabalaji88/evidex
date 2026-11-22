"""POST /api/eval/run (streams progress: test N of 50 and its category) and GET /api/eval/latest."""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..evaluation.runner import latest_path, report_dict, run_suite, save_report, threshold_sweep
from ..schemas import EvalReport, EvalRunRequest
from .sse import SSE_HEADERS, sse

router = APIRouter(prefix="/api")
_running = asyncio.Lock()


@router.post("/eval/run", response_class=StreamingResponse,
             responses={200: {"description": "text/event-stream: `progress` (stage evaluation, done/total, detail), "
                                             "`stage` per run, `error`, and `done` last. Then GET /api/eval/latest.",
                              "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def run_eval(request: Request, body: EvalRunRequest | None = None):
    body = body or EvalRunRequest()
    s = request.app.state
    cfg = s.config

    async def stream():
        t0 = time.perf_counter()
        if _running.locked():
            yield sse("error", {"code": "busy", "message": "An evaluation is already running."})
            yield sse("done", {"elapsed_ms": 0})
            return
        async with _running:
            queue: asyncio.Queue = asyncio.Queue()
            variants = [("config.yaml", cfg)]
            if body.compare:
                for rer in (True, False):
                    for k in (3, 5):
                        if rer == cfg.reranker.enabled and k == cfg.retrieval.final_k:
                            continue
                        variants.append((f"reranker {'on' if rer else 'off'}, final_k {k}", cfg.model_copy(update={
                            "reranker": cfg.reranker.model_copy(update={"enabled": rer}),
                            "retrieval": cfg.retrieval.model_copy(update={"final_k": k})})))

            async def work():
                runs = []
                for i, (label, vcfg) in enumerate(variants, 1):
                    await queue.put(("stage", {"stage": "evaluation", "status": "running",
                                               "detail": f"Run {i} of {len(variants)}: {label}"}))

                    def progress(n, total, case, label=label):
                        queue.put_nowait(("progress", {"stage": "evaluation", "done": n, "total": total,
                                                       "detail": f"Test {n} of {total} · {case['category']}"}))

                    runs.append(await run_suite(vcfg, label=label, reranker_obj=s.reranker_state.reranker,
                                                progress=progress, llm=s.llm, embedder=s.ingestor.embedder))
                main = runs[0]
                thr = cfg.retrieval.relevance_threshold if cfg.reranker.enabled else cfg.retrieval.vector_threshold
                rep = report_dict(main, runs[1:], threshold_sweep(main.cases, thr))
                save_report(cfg, rep)
                m = main.metrics
                await queue.put(("stage", {"stage": "evaluation", "status": "done",
                                           "detail": f"{m['passed']} of {m['total']} passed"}))

            task = asyncio.create_task(work())
            try:
                while not (task.done() and queue.empty()):
                    try:
                        ev, data = await asyncio.wait_for(queue.get(), timeout=0.5)
                        yield sse(ev, data)
                    except asyncio.TimeoutError:
                        continue
                exc = task.exception()
                if exc is not None:
                    yield sse("error", {"code": "eval_failed", "message": f"The evaluation failed: {exc}"})
            finally:
                if not task.done():
                    task.cancel()
                yield sse("done", {"elapsed_ms": int((time.perf_counter() - t0) * 1000)})

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/eval/latest", response_model=EvalReport)
async def latest(request: Request) -> EvalReport:
    p = latest_path(request.app.state.config)
    if not p.is_file():
        raise HTTPException(404, "No evaluation has been run yet. Run it from the Evaluation page or "
                                 "eval/run_eval.py.")
    return EvalReport(**json.loads(p.read_text(encoding="utf-8")))
