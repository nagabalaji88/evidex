"""Evaluation (spec section 7): run the 50-case test set against the real pipeline, score it,
compare settings, sweep the relevance threshold, and write eval/report.md.

Every run indexes eval/sample_docs into a fresh, separate store (never your knowledge base):
all documents, with refund_policy.docx uploaded as v1 then v2 for the version traps.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..answer.ask import AskService
from ..answer.schema import REFUSAL_PHRASE
from ..config import REPO_ROOT, Config, StorageConfig
from ..grounding.normalise import normalise_number, numbers_and_dates
from ..ingestion.pipeline import Ingestor
from ..ollama_client import OllamaClient
from ..retrieval.retriever import Retriever
from ..setup_check import GB, PeakSampler
from ..store.keyword import KeywordIndex
from ..store.sqlite import Database
from ..store.vectors import VectorStore, open_vector_store

EVAL_DIR = REPO_ROOT / "eval"
TESTSET = EVAL_DIR / "testset.json"
SAMPLE_DOCS = EVAL_DIR / "sample_docs"
REPORT_MD = EVAL_DIR / "report.md"

# Pass targets (spec section 7). These are the evaluation's acceptance limits.
TARGETS = {"refusal_accuracy": 1.0, "answer_correctness": 0.9, "citation_accuracy": 0.9, "version_accuracy": 1.0,
           "table_exact": 1.0, "injection_ok": 1.0, "broken_ok": 1.0, "avg_answer_s": 30.0, "avg_retrieval_s": 5.0}

DEPARTMENTS = {"employee_handbook.pdf": "Retail Operations", "supplier_code_of_conduct.docx": "Procurement",
               "refund_policy.docx": "Customer Care", "store_operations_manual.md": "Retail Operations",
               "delivery_partner_faq.txt": "Logistics", "sales_q3.csv": "Finance", "inventory.xlsx": "Merchandising",
               "stores.json": "Retail Operations", "loyalty_programme.json": "Marketing"}

Progress = Callable[[int, int, dict[str, Any]], Awaitable[None] | None]


@dataclass
class CaseResult:
    id: str
    category: str
    question: str
    passed: bool
    reason: str
    answer: str = ""
    kind: str = ""
    refused: bool = False
    correct: bool | None = None
    citation_ok: bool | None = None
    cited: list[str] = field(default_factory=list)
    best_score: float | None = None
    gate_passed: bool | None = None
    total_ms: int = 0
    retrieval_ms: int = 0
    ai_called: bool = False


@dataclass
class RunResult:
    label: str
    settings: dict[str, Any]
    cases: list[CaseResult]
    metrics: dict[str, Any]
    peak_ram_gb: float
    started: str
    seconds: float


def load_testset(path: Path = TESTSET) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if len(cases) != 50:
        raise ValueError(f"the test set must have exactly 50 cases, found {len(cases)}")
    return cases


# -- scoring --------------------------------------------------------------------------------


def contains_fact(answer: str, alternatives: list[str]) -> bool:
    """A fact matches if any alternative appears; numbers are compared after normalisation."""
    low = answer.lower()
    nums, dates = numbers_and_dates(answer)
    for alt in alternatives:
        if alt.lower() in low:
            return True
        alt_nums, alt_dates = numbers_and_dates(alt)
        words = [w for w in alt.lower().split() if not any(ch.isdigit() for ch in w)]
        if (alt_nums or alt_dates) and alt_nums <= nums and alt_dates <= dates and all(w in low for w in words):
            return True
    return False


def citation_matches(case: dict[str, Any], evidence: list[dict[str, Any]]) -> bool:
    doc = case.get("expected_doc")
    if not doc:
        return True
    hits = [e for e in evidence if e["doc_name"] == doc]
    if not hits:
        return False
    if "expected_version" in case and not all(e["version"] == case["expected_version"] for e in hits):
        return False
    page = case.get("expected_page")
    if page is not None:
        pages = page if isinstance(page, list) else [page]
        return any(e.get("page") in pages for e in hits)
    loc = case.get("expected_location")
    if loc:
        return any(loc.lower() in (e.get("source_location") or "").lower() for e in hits)
    return True


def score_case(case: dict[str, Any], answer: dict[str, Any] | None, error: dict[str, Any] | None,
               trace: dict[str, Any] | None) -> CaseResult:
    r = CaseResult(case["id"], case["category"], case["question"], False, "")
    if trace:
        r.best_score, r.gate_passed = trace.get("best_score"), trace.get("gate_passed")
        r.retrieval_ms = trace.get("retrieval_ms") or 0
    if error is not None:
        r.reason = f"error: {error['code']}: {error['message']}"
        return r
    text = answer["answer"]
    r.answer, r.kind, r.ai_called = text, answer["kind"], answer["ai_called"]
    r.total_ms = answer.get("elapsed_ms", 0)
    r.refused = answer["kind"] == "refusal"
    if r.refused and text != REFUSAL_PHRASE:
        r.reason = "refusal does not use the exact phrase"
        return r
    evidence = answer.get("evidence", [])
    r.cited = sorted({f"{e['doc_name']} v{e['version']}" + (f" p{e['page']}" if e.get("page") else "")
                      for e in evidence})
    cat = case["category"]
    forbidden = [f for f in case.get("forbidden", []) if f.lower() in text.lower()]
    if case["expected_refusal"]:
        r.passed = r.refused and not forbidden
        r.reason = "" if r.passed else (f"answered instead of refusing: {text[:160]}" if not r.refused
                                        else f"forbidden text {forbidden}")
        return r
    if r.refused:
        r.correct = False
        r.reason = f"refused an answerable question (best score {r.best_score})"
        return r
    missing = [alts for alts in case["expected"] if not contains_fact(text, alts)]
    if cat == "table":
        expected = case["expected_value"]
        cells = [v for row in (answer.get("sql") or {}).get("rows", []) for v in row]
        exact = any(_same_value(v, expected) for v in cells)
        r.correct = exact
        r.citation_ok = (answer.get("sql") or {}).get("file") == case["expected_doc"]
        r.passed = exact and r.citation_ok
        r.reason = "" if r.passed else (f"result {cells[:5]} does not contain {expected}" if not exact
                                        else "wrong table")
        return r
    r.correct = not missing and not forbidden
    r.citation_ok = citation_matches(case, evidence)
    r.passed = r.correct and r.citation_ok
    if not r.passed:
        parts = []
        if missing:
            parts.append(f"missing {['/'.join(m) for m in missing]}")
        if forbidden:
            parts.append(f"contains forbidden {forbidden}")
        if not r.citation_ok:
            parts.append(f"cited {r.cited or 'nothing'}, expected {case.get('expected_doc')} "
                         f"{case.get('expected_page') or case.get('expected_location') or ''}".strip())
        r.reason = "; ".join(parts) + f" | answer: {text[:160]}"
    return r


def _same_value(cell: Any, expected: Any) -> bool:
    if isinstance(expected, str):
        return str(cell).strip().lower() == expected.lower()
    a = normalise_number(str(cell)) if not isinstance(cell, float) else normalise_number(repr(cell))
    return a == normalise_number(str(expected))


def metrics_for(cases: list[CaseResult]) -> dict[str, Any]:
    def share(cat: str, attr: str = "passed") -> float | None:
        xs = [c for c in cases if c.category == cat]
        return round(sum(bool(getattr(c, attr)) for c in xs) / len(xs), 4) if xs else None

    answered = [c for c in cases if c.category != "broken" and c.total_ms]
    with_retrieval = [c for c in answered if c.retrieval_ms]
    m = {
        "passed": sum(c.passed for c in cases), "total": len(cases),
        "refusal_accuracy": share("unanswerable"),
        "answer_correctness": share("answerable", "correct"),
        "citation_accuracy": share("answerable", "citation_ok"),
        "version_accuracy": share("version"),
        "table_exact": share("table"),
        "injection_ok": share("injection"),
        "broken_ok": share("broken"),
        "avg_answer_s": round(sum(c.total_ms for c in answered) / len(answered) / 1000, 2) if answered else None,
        "slowest_answer_s": round(max((c.total_ms for c in answered), default=0) / 1000, 2),
        "avg_retrieval_s": round(sum(c.retrieval_ms for c in with_retrieval) / len(with_retrieval) / 1000, 3)
        if with_retrieval else None,
        "slowest_retrieval_s": round(max((c.retrieval_ms for c in with_retrieval), default=0) / 1000, 3),
    }
    m["targets_met"] = {k: _meets(k, m.get(k)) for k in TARGETS}
    return m


def _meets(key: str, value: Any) -> bool | None:
    if value is None:
        return None
    target = TARGETS[key]
    return value <= target if key.startswith("avg_") else value >= target


# -- threshold sweep ------------------------------------------------------------------------


def threshold_sweep(cases: list[CaseResult], current: float) -> dict[str, Any]:
    """Gate-level sweep: the gate depends only on the best score, so each threshold's
    effect is computed from the recorded scores without re-running the model."""
    unans = [c for c in cases if c.category == "unanswerable" and c.best_score is not None]
    answ = [c for c in cases if c.category in ("answerable", "version") and c.best_score is not None]
    rows = []
    for i in range(5, 96):
        t = round(i / 100, 2)
        refused_unans = sum(c.best_score < t for c in unans)
        blocked_answ = sum(c.best_score < t for c in answ)
        rows.append({"threshold": t, "unanswerable_refused_by_gate": refused_unans,
                     "unanswerable_total": len(unans), "answerable_blocked": blocked_answ,
                     "answerable_total": len(answ)})
    full = [r for r in rows if r["unanswerable_refused_by_gate"] == r["unanswerable_total"]]
    best = min(full, key=lambda r: (r["answerable_blocked"], r["threshold"])) if full else None
    return {"current": current, "rows": rows, "recommended": best["threshold"] if best else None,
            "recommended_blocks": best["answerable_blocked"] if best else None,
            "max_unanswerable_score": max((c.best_score for c in unans), default=None),
            "min_answerable_score": min((c.best_score for c in answ), default=None)}


# -- running ----------------------------------------------------------------------------------


def variant(config: Config, work: Path, *, reranker: bool | None = None, final_k: int | None = None) -> Config:
    storage = StorageConfig(vector_store=str(work / "chroma"), sqlite=str(work / "app.db"))
    upd: dict[str, Any] = {"storage": storage}
    if reranker is not None:
        upd["reranker"] = config.reranker.model_copy(update={"enabled": reranker})
    if final_k is not None:
        upd["retrieval"] = config.retrieval.model_copy(update={"final_k": final_k})
    return config.model_copy(update=upd)


async def run_suite(config: Config, *, label: str, reranker_obj=None, cases: list[dict[str, Any]] | None = None,
                    work_root: Path | None = None, progress: Progress | None = None, llm: OllamaClient | None = None,
                    embedder=None) -> RunResult:
    cases = cases or load_testset()
    work_root = work_root or (config.sqlite_path.parent / "eval_work")
    work = work_root / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    work.mkdir(parents=True, exist_ok=True)
    cfg = variant(config, work)
    own_llm = llm is None
    llm = llm or OllamaClient(cfg)
    embedder = embedder or llm
    db = Database(cfg.sqlite_path)
    vectors = VectorStore(open_vector_store(cfg))
    ingestor = Ingestor(cfg, db, vectors, embedder)
    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.perf_counter()
    results: list[CaseResult] = []
    try:
        with PeakSampler() as peak:
            await _index_samples(ingestor, cases)
            keywords = KeywordIndex(db)
            retriever = Retriever(cfg, db, vectors, keywords, embedder,
                                  reranker_obj if cfg.reranker.enabled else None, llm)
            svc = AskService(cfg, db, retriever, llm)
            for n, case in enumerate(cases, 1):
                if progress is not None:
                    maybe = progress(n, len(cases), case)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                results.append(await _run_case(case, svc, ingestor, db))
        return RunResult(label, _settings(cfg), results, metrics_for(results), round(peak.peak / GB, 2), started,
                         round(time.perf_counter() - t0, 1))
    finally:
        db.close()
        if own_llm:
            await llm.aclose()
        shutil.rmtree(work, ignore_errors=True)


async def _index_samples(ingestor: Ingestor, cases) -> None:
    order = sorted(p for p in SAMPLE_DOCS.glob("*") if p.is_file() and p.name in DEPARTMENTS)
    order += [SAMPLE_DOCS / "versions" / "v1" / "refund_policy.docx", SAMPLE_DOCS / "versions" / "v2" /
              "refund_policy.docx"]
    for path in order:
        events = [e async for e in ingestor.ingest(path.name, path.read_bytes(), DEPARTMENTS[path.name], "Evaluation")]
        final = next(e for e in events if e.event in ("result", "error"))
        if final.event == "error":
            raise RuntimeError(f"could not index sample {path.name}: {final.data['message']}")


async def _run_case(case: dict[str, Any], svc: AskService, ingestor: Ingestor, db: Database) -> CaseResult:
    if case["category"] == "broken":
        path = SAMPLE_DOCS / case["file"]
        events = [e async for e in ingestor.ingest(path.name, path.read_bytes(), "Finance", "Evaluation")]
        final = next(e for e in events if e.event in ("result", "error"))
        ok = final.event == "error" and final.data["code"] == case["expected_error"] and \
            final.data["message"] == case.get("expected_message", final.data["message"])
        return CaseResult(case["id"], "broken", case["question"], ok,
                          "" if ok else f"expected error {case['expected_error']}, got {final.event} {final.data}",
                          answer=final.data.get("message", ""))
    answer = error = None
    async for ev in svc.ask(case["question"], session_id=f"eval-{case['id']}"):
        if ev.event == "answer":
            answer = ev.data
        elif ev.event == "error":
            error = ev.data
    row = db.one("SELECT * FROM question_log ORDER BY id DESC LIMIT 1")
    trace = dict(row) if row is not None else None
    res = score_case(case, answer, error, trace)
    if error is not None and trace:
        res.total_ms = trace.get("total_ms") or 0
    return res


def _settings(cfg: Config) -> dict[str, Any]:
    return {"answer_model": cfg.models.answer, "embedding_model": cfg.models.embedding,
            "reranker": cfg.reranker.enabled, "final_k": cfg.retrieval.final_k,
            "candidate_k": cfg.retrieval.candidate_k, "relevance_threshold": cfg.retrieval.relevance_threshold,
            "vector_threshold": cfg.retrieval.vector_threshold, "num_ctx": cfg.ollama.num_ctx,
            "chunk_size_tokens": cfg.ingestion.chunk_size_tokens}


# -- report -------------------------------------------------------------------------------------


def report_dict(main: RunResult, comparisons: list[RunResult], sweep: dict[str, Any] | None) -> dict[str, Any]:
    def run_d(r: RunResult) -> dict[str, Any]:
        d = asdict(r)
        return d

    cheapest = _cheapest([main] + comparisons)
    return {"created_at": datetime.now().isoformat(timespec="seconds"), "main": run_d(main),
            "comparisons": [run_d(c) for c in comparisons], "sweep": sweep, "cheapest_passing": cheapest,
            "targets": TARGETS}


def _cheapest(runs: list[RunResult]) -> dict[str, Any] | None:
    """Cheapest setting that still meets the 100% refusal target: reranker off before on,
    then the smaller final_k."""
    ok = [r for r in runs if r.metrics.get("refusal_accuracy") == 1.0]
    if not ok:
        return None
    best = min(ok, key=lambda r: (r.settings["reranker"], r.settings["final_k"]))
    return {"label": best.label, "settings": best.settings}


def _pct(v):
    return "n/a" if v is None else f"{v * 100:.0f}%"


def render_report(rep: dict[str, Any]) -> str:
    main = rep["main"]
    m = main["metrics"]
    tm = m["targets_met"]
    mark = lambda k: "PASS" if tm.get(k) else ("n/a" if tm.get(k) is None else "FAIL")  # noqa: E731
    lines = [
        "# Evaluation report", "",
        f"Generated by `eval/run_eval.py` on {rep['created_at']}. Run started {main['started']} and took "
        f"{main['seconds']:.0f} s.", "",
        f"**Result: {m['passed']} of {m['total']} cases passed.**", "",
        "Settings: " + ", ".join(f"{k} `{v}`" for k, v in main["settings"].items()), "",
        "## Metrics against targets (spec section 7)", "",
        "| Metric | Result | Target | Met |", "| --- | --- | --- | --- |",
        f"| Refusal accuracy (unanswerable refused) | {_pct(m['refusal_accuracy'])} | 100% | {mark('refusal_accuracy')} |",
        f"| Answer correctness (answerable) | {_pct(m['answer_correctness'])} | 90% or higher | {mark('answer_correctness')} |",
        f"| Citation accuracy (right doc and page) | {_pct(m['citation_accuracy'])} | 90% or higher | {mark('citation_accuracy')} |",
        f"| Version accuracy (no old-version answers) | {_pct(m['version_accuracy'])} | 100% | {mark('version_accuracy')} |",
        f"| Table answers exact | {_pct(m['table_exact'])} | 100% | {mark('table_exact')} |",
        f"| Prompt injection ignored | {_pct(m['injection_ok'])} | 100% | {mark('injection_ok')} |",
        f"| Broken files rejected cleanly | {_pct(m['broken_ok'])} | 100% | {mark('broken_ok')} |",
        f"| Average full answer (slowest) | {m['avg_answer_s']} s ({m['slowest_answer_s']} s) | under 30 s | "
        f"{mark('avg_answer_s')} |",
        f"| Average retrieval + rerank (slowest) | {m['avg_retrieval_s']} s ({m['slowest_retrieval_s']} s) | under 5 s "
        f"| {mark('avg_retrieval_s')} |",
        f"| Peak RAM during the run (system-wide) | {main['peak_ram_gb']} GB | under 12 GB | "
        f"{'PASS' if main['peak_ram_gb'] < 12 else 'FAIL'} |", "",
    ]
    if rep["comparisons"]:
        lines += ["## Comparison runs", "",
                  "| Run | Reranker | final_k | Passed | Refusal | Correct | Citations | Avg / slowest answer | "
                  "Avg retrieval | Peak RAM |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for r in [main] + rep["comparisons"]:
            x = r["metrics"]
            lines.append(f"| {r['label']} | {'on' if r['settings']['reranker'] else 'off'} | {r['settings']['final_k']} "
                         f"| {x['passed']}/{x['total']} | {_pct(x['refusal_accuracy'])} | "
                         f"{_pct(x['answer_correctness'])} | {_pct(x['citation_accuracy'])} | {x['avg_answer_s']} s / "
                         f"{x['slowest_answer_s']} s | {x['avg_retrieval_s']} s | {r['peak_ram_gb']} GB |")
        c = rep["cheapest_passing"]
        if c:
            lines += ["", f"Cheapest setting that still refuses 100% of unanswerable questions: **{c['label']}** "
                          f"(reranker {'on' if c['settings']['reranker'] else 'off'}, final_k "
                          f"{c['settings']['final_k']}). Record it in config.yaml.", ""]
        else:
            lines += ["", "No compared setting reached 100% refusal accuracy.", ""]
    sw = rep.get("sweep")
    if sw:
        lines += ["## Threshold sweep", "",
                  f"Current threshold: {sw['current']}. Highest unanswerable best score: {sw['max_unanswerable_score']}. "
                  f"Lowest answerable best score: {sw['min_answerable_score']}.", "",
                  (f"Recommended threshold: **{sw['recommended']}** (refuses every unanswerable question at the gate, "
                   f"blocks {sw['recommended_blocks']} answerable)." if sw["recommended"] is not None else
                   "No threshold refuses every unanswerable question at the gate."), "",
                  "| Threshold | Unanswerable refused at the gate | Answerable blocked |", "| --- | --- | --- |"]
        for r in sw["rows"]:
            if round(r["threshold"] * 100) % 5 == 0 or r["threshold"] == sw["recommended"]:
                lines.append(f"| {r['threshold']:.2f} | {r['unanswerable_refused_by_gate']}/{r['unanswerable_total']} "
                             f"| {r['answerable_blocked']}/{r['answerable_total']} |")
        lines.append("")
    failures = [c for c in main["cases"] if not c["passed"]]
    lines += ["## Failures", ""]
    lines += [f"- **{c['id']}** ({c['category']}) {c['question']}: {c['reason']}" for c in failures] or ["None."]
    lines += ["", "## Every case", "", "| Case | Category | Passed | Best score | Time | Cited | Answer |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for c in main["cases"]:
        ans = (c["answer"] or "").replace("|", "/").replace("\n", " ")[:90]
        score = "" if c["best_score"] is None else f"{c['best_score']:.2f}"
        lines.append(f"| {c['id']} | {c['category']} | {'yes' if c['passed'] else 'NO'} | {score} | "
                     f"{c['total_ms'] / 1000:.1f} s | {', '.join(c['cited'])} | {ans} |")
    return "\n".join(lines) + "\n"


def latest_path(config: Config) -> Path:
    return config.sqlite_path.parent / "eval" / "latest.json"


def save_report(config: Config, rep: dict[str, Any], *, write_md: bool = True) -> None:
    p = latest_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    if write_md:
        REPORT_MD.write_text(render_report(rep), encoding="utf-8")
