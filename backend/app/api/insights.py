"""GET /api/stats (Dashboard) and GET /api/activity (Activity log). Every number comes
from the log tables (question_log, uploads), never hard-coded."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal

import psutil
from fastapi import APIRouter, Request

from ..evaluation.runner import latest_path
from ..health import run_health_checks
from ..schemas import ActivityList, ActivityRow, DayCount, NameCount, Stats

router = APIRouter(prefix="/api")

ANSWERED = ("answered", "partial")
REFUSED = ("not_in_documents", "refused_grounding")


def _result(outcome: str) -> str:
    return "answered" if outcome in ANSWERED else "refused" if outcome in REFUSED else "error"


@router.get("/stats", response_model=Stats)
async def stats(request: Request, days: int = 7) -> Stats:
    s = request.app.state
    db = s.db
    days = max(1, min(days, 365))
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days - 1)).date()
    since_iso = since.isoformat()
    docs = db.one("SELECT COUNT(*) AS n FROM versions WHERE status = 'active'")["n"]
    versions = db.one("SELECT COUNT(*) AS n FROM versions WHERE status IN ('active', 'inactive')")["n"]
    chunks = db.one("SELECT COUNT(*) AS n FROM chunks c JOIN versions v ON v.id = c.version_id "
                    "WHERE c.status = 'active' AND v.path = 'text'")["n"]
    rows = db.one("SELECT COALESCE(SUM(table_rows), 0) AS n FROM versions WHERE status = 'active'")["n"]
    q = db.query("SELECT * FROM question_log WHERE substr(created_at, 1, 10) >= ? ORDER BY id", (since_iso,))
    results = [_result(r["outcome"]) for r in q]
    times = sorted(r["total_ms"] for r in q if r["total_ms"] and _result(r["outcome"]) != "error")
    confs = [r["confidence"] for r in q if r["confidence"] is not None and r["confidence_level"] in
             ("High", "Medium", "Low")]
    per_day = []
    for i in range(days):
        d = (since + timedelta(days=i)).isoformat()
        day = [_result(r["outcome"]) for r in q if r["created_at"][:10] == d]
        per_day.append(DayCount(date=d, answered=day.count("answered"), refused=day.count("refused"),
                                errors=day.count("error")))
    levels = Counter(r["confidence_level"] for r in q if _result(r["outcome"]) == "answered" and r["confidence_level"])
    types = Counter(r["file_type"].upper() for r in db.query(
        "SELECT d.file_type FROM documents d JOIN versions v ON v.document_id = d.id AND v.status = 'active'"))
    cited: Counter = Counter()
    for r in db.query("SELECT cited_documents FROM question_log WHERE cited_documents IS NOT NULL"):
        for name in json.loads(r["cited_documents"] or "[]"):
            cited[name] += 1
    gaps = Counter(r["question"].strip() for r in q if _result(r["outcome"]) == "refused")
    health = await run_health_checks(s.config, s.ollama, s.reranker_state, lambda: s.vectors)
    last_eval, peak = None, None
    p = latest_path(s.config)
    if p.is_file():
        rep = json.loads(p.read_text(encoding="utf-8"))
        m = rep["main"]["metrics"]
        last_eval = {"created_at": rep["created_at"], "passed": m["passed"], "total": m["total"],
                     "targets_met": sum(1 for v in m["targets_met"].values() if v),
                     "targets": sum(1 for v in m["targets_met"].values() if v is not None)}
        peak = rep["main"]["peak_ram_gb"]

    def p95(xs):
        return round(xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))] / 1000, 1) if xs else None

    cfg = s.config
    return Stats(
        days=days, since=since_iso, documents=docs, versions=versions, kept_for_rollback=versions - docs,
        chunks=chunks, table_rows=rows, questions=len(q), answered=results.count("answered"),
        not_in_documents=results.count("refused"), errors=results.count("error"),
        avg_response_s=round(sum(times) / len(times) / 1000, 1) if times else None, p95_response_s=p95(times),
        avg_confidence=round(sum(confs) / len(confs), 2) if confs else None, per_day=per_day,
        confidence=[NameCount(name=k, count=levels.get(k, 0)) for k in ("High", "Medium", "Low", "Exact")],
        document_types=[NameCount(name=k, count=v) for k, v in types.most_common()],
        most_cited=[NameCount(name=k, count=v) for k, v in cited.most_common(6)],
        gaps=[NameCount(name=k, count=v) for k, v in gaps.most_common(5)],
        health=health, last_eval=last_eval, peak_ram_gb=peak,
        total_ram_gb=round(psutil.virtual_memory().total / 1024 ** 3, 1),
        thresholds={"relevance_threshold": cfg.retrieval.relevance_threshold,
                    "vector_threshold": cfg.retrieval.vector_threshold, "high_from": cfg.confidence.high_from,
                    "medium_from": cfg.confidence.medium_from})


@router.get("/activity", response_model=ActivityList)
async def activity(request: Request, result: Literal["all", "answered", "refused", "error", "upload"] = "all",
                   limit: int = 200) -> ActivityList:
    db = request.app.state.db
    rows: list[ActivityRow] = []
    if result != "upload":
        for r in db.query("SELECT * FROM question_log ORDER BY id DESC LIMIT ?", (limit,)):
            res = _result(r["outcome"])
            if result not in ("all", res):
                continue
            level = r["confidence_level"]
            conf = "Exact" if level == "Exact" else (f"{r['confidence']:.2f}" if r["confidence"] is not None else
                                                     (f"{r['best_score']:.2f}" if r["best_score"] is not None else "—"))
            cited = json.loads(r["cited_documents"] or "[]") if r["cited_documents"] else []
            rows.append(ActivityRow(kind="question", id=r["id"], when=r["created_at"], detail=r["question"], result=res,
                                    confidence=conf, source=", ".join(cited) or "None", ms=r["total_ms"],
                                    trace_id=r["id"], feedback=r["feedback"]))
    if result in ("all", "upload"):
        for u in db.query("SELECT u.*, v.version AS version, v.chunk_count AS chunks, v.table_rows AS trows "
                          "FROM uploads u LEFT JOIN versions v ON v.id = u.version_id ORDER BY u.id DESC LIMIT ?",
                          (limit,)):
            detail = u["filename"] + (f" v{u['version']}" if u["version"] else "")
            if u["result"] == "indexed" and u["chunks"] is not None:
                detail += f" · {u['trows']:,} rows" if u["trows"] else f" · {u['chunks']} chunks"
            elif u["message"]:
                detail += f" · {u['message']}"
            done = next((e["data"].get("elapsed_ms") for e in json.loads(u["steps"]) if e["event"] in
                         ("result",)), None)
            rows.append(ActivityRow(kind="upload", id=u["id"], when=u["uploaded_at"], detail=detail,
                                    result=u["result"], confidence="—", source=u["department"] or "",
                                    ms=done))
    rows.sort(key=lambda r: r.when, reverse=True)
    return ActivityList(rows=rows[:limit])
