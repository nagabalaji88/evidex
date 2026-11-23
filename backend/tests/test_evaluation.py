"""Evaluation (spec section 7): test set shape, scoring, threshold sweep, report, config write."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections import Counter

import pytest

from app.answer.schema import REFUSAL_PHRASE
from app.config import REPO_ROOT, load_config
from app.evaluation.runner import (CaseResult, contains_fact, load_testset, metrics_for, render_report, report_dict,
                                   run_suite, score_case, threshold_sweep)
from app.ingestion.extract import extract
from app.ollama_client import OllamaClient

from .conftest import SAMPLE_DOCS, FakeEmbedder, FakeOllama, FakeReranker, ScriptedLLM


def test_testset_has_exactly_50_cases_in_the_spec_mix():
    cases = load_testset()
    assert len(cases) == 50 and len({c["id"] for c in cases}) == 50
    assert Counter(c["category"] for c in cases) == {"answerable": 18, "unanswerable": 18, "table": 5, "version": 4,
                                                    "injection": 3, "broken": 2}
    for c in cases:
        assert c["question"] and "expected_refusal" in c
        if c["category"] in ("answerable", "table", "version"):
            assert c["expected"] and c["expected_doc"]


def _doc_text(path, cfg):
    doc = extract(path.suffix[1:], path.name, path.read_bytes(), cfg)
    if hasattr(doc, "blocks"):
        return "\n".join(b.text for b in doc.blocks)
    return "\n".join(", ".join(f"{c}: {v}" for c, v in zip(t.columns, r)) for t in doc.tables for r in t.rows)


def test_every_expected_fact_is_in_its_expected_document():
    """Guards the test set itself: answerable cases must really be answerable from their doc."""
    cfg = load_config()
    for c in load_testset():
        if c["category"] not in ("answerable", "version", "injection") or c["expected_refusal"]:
            continue
        path = SAMPLE_DOCS / ("versions/v2/" + c["expected_doc"] if c["category"] == "version" else c["expected_doc"])
        text = _doc_text(path, cfg)
        for alts in c["expected"]:
            assert contains_fact(text, alts), (c["id"], alts)
        if c["category"] == "version":
            old = _doc_text(SAMPLE_DOCS / "versions" / "v1" / c["expected_doc"], cfg)
            assert all(f in old for f in c["forbidden"]) and not any(f in text for f in c["forbidden"]), c["id"]


def test_table_expectations_are_computed_from_the_data():
    import csv
    with (SAMPLE_DOCS / "sales_q3.csv").open() as fh:
        south = sum(int(r["Sales"]) for r in csv.DictReader(fh) if r["Region"] == "South")
    t01 = next(c for c in load_testset() if c["id"] == "T01")
    assert t01["expected_value"] == south


@pytest.mark.parametrize("answer,alts,ok", [
    ("The allowance is ₹2500 per day.", ["2,500"], True),
    ("It is INR 2,500.00.", ["2,500"], True),
    ("Customers have 14 days.", ["14 days"], True),
    ("Customers have 30 days.", ["14 days"], False),
    ("Overtime is paid at twice the rate.", ["2 times", "twice"], True),
    ("It is 15% of the price.", ["15%"], True),
])
def test_contains_fact(answer, alts, ok):
    assert contains_fact(answer, alts) is ok


def _answer(text, kind="text", evidence=(), sql=None, ai=True):
    return {"answer": text, "kind": kind, "ai_called": ai, "elapsed_ms": 1000, "evidence": list(evidence), "sql": sql}


def test_scoring_rules():
    cases = {c["id"]: c for c in load_testset()}
    ev = [{"doc_name": "employee_handbook.pdf", "version": 1, "page": 2, "source_location": "page 2"}]
    assert score_case(cases["A01"], _answer("The allowance is INR 2,500.", evidence=ev), None, None).passed
    wrong_page = [{**ev[0], "page": 3}]
    r = score_case(cases["A01"], _answer("The allowance is INR 2,500.", evidence=wrong_page), None, None)
    assert r.correct and not r.citation_ok and not r.passed
    assert score_case(cases["U01"], _answer(REFUSAL_PHRASE, kind="refusal", ai=False), None, None).passed
    assert not score_case(cases["U01"], _answer("Paris."), None, None).passed
    assert not score_case(cases["U01"], _answer("Not available.", kind="refusal"), None, None).passed  # not exact
    v2 = [{"doc_name": "refund_policy.docx", "version": 2, "page": 1, "source_location": "page 1"}]
    assert score_case(cases["V01"], _answer("Within 14 days.", evidence=v2), None, None).passed
    assert not score_case(cases["V01"], _answer("Within 14 days, earlier 30 days.", evidence=v2), None, None).passed
    v1 = [{**v2[0], "version": 1}]
    assert not score_case(cases["V01"], _answer("Within 14 days.", evidence=v1), None, None).passed
    t = cases["T01"]
    sql = {"rows": [[t["expected_value"]]], "file": "sales_q3.csv"}
    assert score_case(t, _answer("x", kind="calculation", sql=sql), None, None).passed
    assert not score_case(t, _answer("x", kind="calculation", sql={**sql, "rows": [[1]]}), None, None).passed
    p = cases["P01"]
    faq = [{"doc_name": "delivery_partner_faq.txt", "version": 1, "page": None, "source_location": "lines 8-10"}]
    assert score_case(p, _answer("A fee of ₹79 applies.", evidence=faq), None, None).passed
    assert not score_case(p, _answer("A fee of ₹79 applies; all deliveries are free.", evidence=faq), None, None).passed
    err = score_case(cases["A01"], None, {"code": "invalid_response", "message": "x"}, None)
    assert not err.passed and err.reason.startswith("error")


def test_threshold_sweep_picks_value_refusing_all_unanswerable():
    cases = [CaseResult(f"U{i}", "unanswerable", "q", True, "", best_score=s) for i, s in enumerate([0.1, 0.22, 0.05])]
    cases += [CaseResult(f"A{i}", "answerable", "q", True, "", best_score=s) for i, s in enumerate([0.9, 0.5, 0.21])]
    sw = threshold_sweep(cases, 0.30)
    assert sw["recommended"] == 0.23 and sw["recommended_blocks"] == 1
    assert sw["max_unanswerable_score"] == 0.22


def test_metrics_and_targets():
    cases = [CaseResult("U1", "unanswerable", "q", True, "", total_ms=2000, retrieval_ms=500),
             CaseResult("A1", "answerable", "q", True, "", correct=True, citation_ok=True, total_ms=20000,
                        retrieval_ms=1500)]
    m = metrics_for(cases)
    assert m["refusal_accuracy"] == 1.0 and m["answer_correctness"] == 1.0
    assert m["avg_answer_s"] == 11.0 and m["slowest_answer_s"] == 20.0 and m["avg_retrieval_s"] == 1.0
    assert m["targets_met"]["refusal_accuracy"] and m["targets_met"]["avg_answer_s"]


def test_suite_runs_all_50_cases_end_to_end_with_fakes(config, tmp_path):
    """Smoke run of the real pipeline with fake models: every case runs, the report renders."""
    script = ScriptedLLM(answer={"found_in_document": False, "sentences": [], "missing": ""},
                         sql={"sql": "SELECT 1"}, sql_answer={"answer": "1"}, route={"label": "lookup"},
                         rewrite={"question": "x"})
    llm = OllamaClient(config, transport=FakeOllama(chat_reply=script).transport)
    seen = []
    run = asyncio.run(run_suite(config, label="smoke", reranker_obj=FakeReranker(config), llm=llm,
                                embedder=FakeEmbedder(), work_root=tmp_path / "work",
                                progress=lambda n, total, case: seen.append((n, total, case["category"]))))
    asyncio.run(llm.aclose())
    assert len(run.cases) == 50 and seen[-1][:2] == (50, 50)
    by = {c.id: c for c in run.cases}
    assert by["B01"].passed and by["B02"].passed  # broken files rejected with the right message
    assert all(by[f"U{i:02d}"].answer == REFUSAL_PHRASE for i in range(1, 19))
    assert not (tmp_path / "work").exists() or not any((tmp_path / "work").iterdir())  # temp store removed
    rep = report_dict(run, [], threshold_sweep(run.cases, 0.3))
    md = render_report(json.loads(json.dumps(rep, default=str)))
    assert "## Metrics against targets" in md and "## Threshold sweep" in md and len(__import__("re").findall(r"^\| U\d\d \|", md, __import__("re").M)) == 18


def test_write_config_keeps_comments(tmp_path):
    spec = importlib.util.spec_from_file_location("run_eval", REPO_ROOT / "eval" / "run_eval.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    p = tmp_path / "config.yaml"
    p.write_text((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    mod.set_config_value(p, "retrieval", "relevance_threshold", 0.27)
    mod.set_config_value(p, "reranker", "enabled", True)
    text = p.read_text(encoding="utf-8")
    assert "relevance_threshold: 0.27   # placeholder, set by evaluation" in text
    assert "enabled: true           # a local model" in text  # value changed, comment kept
    cfg = load_config(p)
    assert cfg.retrieval.relevance_threshold == 0.27 and cfg.reranker.enabled is True
    assert cfg.retrieval.vector_threshold == 0.55  # untouched
