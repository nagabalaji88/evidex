"""Spec section 5 acceptance criteria: retrieval, routing, rewrite and the threshold gate."""

from __future__ import annotations

import asyncio
import json

import pytest

from app.logs.questions import log_question
from app.ollama_client import OllamaClient
from app.retrieval.retriever import RetrievalError, Retriever
from app.retrieval.route import route_by_rules

from .conftest import SAMPLE_DOCS, Env, FakeOllama, FakeReranker, make_config, result_of

V1 = SAMPLE_DOCS / "versions" / "v1" / "refund_policy.docx"
V2 = SAMPLE_DOCS / "versions" / "v2" / "refund_policy.docx"
TEXT_DOCS = ["employee_handbook.pdf", "supplier_code_of_conduct.docx", "store_operations_manual.md",
             "delivery_partner_faq.txt"]


class Rig:
    def __init__(self, config, ollama: FakeOllama | None = None, reranker=True):
        self.env = Env(config)
        self.ollama = ollama or FakeOllama()
        self.llm = OllamaClient(config, transport=self.ollama.transport)
        self.reranker = FakeReranker(config) if reranker else None
        self.retriever = Retriever(config, self.env.db, self.env.vectors, self.env.keywords, self.env.embedder,
                                   self.reranker, self.llm)

    def load(self, *names, department="Customer Care"):
        out = {}
        for n in names:
            path = n if hasattr(n, "read_bytes") else SAMPLE_DOCS / n
            out[path.name] = result_of(self.env.ingest(path, department=department)).data
        return out

    def ask(self, q, **kw):
        return asyncio.run(self.retriever.retrieve(q, **kw))

    def chat_calls(self):
        return self.ollama.calls("/api/chat")

    def close(self):
        asyncio.run(self.llm.aclose())
        self.env.close()


@pytest.fixture
def rig(config):
    r = Rig(config)
    yield r
    r.close()


# -- AC: no inactive-version chunk is ever returned ----------------------------------------


def test_hybrid_retrieval_never_returns_inactive_version(rig):
    rig.load(V1, V2)
    res = rig.ask("Within how many days can customers return products? returns@kestrel.example 30 days")
    assert res.candidates and {c.row["version"] for c in res.candidates} == {2}
    assert all(c.row["status"] == "active" for c in res.candidates)


# -- AC: a question below the threshold never triggers an LLM call (verify in logs) --------


def test_below_threshold_never_calls_the_llm(rig):
    rig.load(*TEXT_DOCS)
    res = rig.ask("What is the capital of France?", session_id="s1")
    assert res.candidates  # search ran and found the closest passages
    assert res.gate.passed is False and res.gate.best_score < res.gate.threshold
    assert res.passages == []
    assert rig.chat_calls() == []  # no model call at all
    log_id = log_question(rig.env.db, res, session_id="s1", outcome="not_in_documents", answer_llm_called=False)
    row = rig.env.db.one("SELECT * FROM question_log WHERE id = ?", (log_id,))
    assert row["answer_llm_called"] == 0 and json.loads(row["llm_calls"]) == []
    assert row["gate_passed"] == 0 and row["best_score"] < 0.30
    gate = json.loads(row["steps"])[-1]
    assert gate["title"] == "Is the answer in the documents?" and gate["status"] == "blocked"


def test_gate_uses_reranker_score_not_rrf(rig):
    rig.load(*TEXT_DOCS)

    class LowReranker:
        model_name = "x"

        def score(self, q, passages):
            return [0.05] * len(passages)

    rig.retriever.reranker = LowReranker()
    res = rig.ask("What is the daily allowance in metro cities?")
    assert res.candidates and res.candidates[0].rrf_score > 0
    assert res.gate.passed is False and res.gate.method == "reranker" and res.gate.best_score == 0.05


def test_above_threshold_passes_top_final_k(rig):
    rig.load(*TEXT_DOCS)
    res = rig.ask("What is the daily allowance in metro cities?")
    assert res.gate.passed
    assert len(res.passages) == rig.env.config.retrieval.final_k
    assert "INR 2,500" in res.passages[0].row["body"]
    scores = [c.rerank_score for c in res.candidates]
    assert scores == sorted(scores, reverse=True)
    top = res.passages[0].row
    assert top["doc_name"] == "employee_handbook.pdf" and top["version"] == 1 and top["page"] == 2


# -- AC: an exact-term query (clause number or code) finds the right chunk ---------------


@pytest.mark.parametrize("query,expected", [
    ("What does Clause 4.5 say?", "Clause 4.5."),
    ("What is HR-LV-07?", "HR-LV-07"),
    ("Explain rule SCC-11", "Rule SCC-11"),
])
def test_exact_term_finds_its_chunk(rig, query, expected):
    rig.load(*TEXT_DOCS)
    res = rig.ask(query)
    assert expected in res.candidates[0].row["body"]
    assert "keyword" in res.candidates[0].via


def test_keyword_search_alone_ranks_code_first(rig):
    rig.load(*TEXT_DOCS)
    hits = rig.env.keywords.search("HR-LV-07", 3)
    assert "HR-LV-07" in rig.env.db.chunks_by_ids([hits[0].chunk_id])[hits[0].chunk_id]["body"]


# -- AC: a follow-up question retrieves the same document as its original question --------


def test_follow_up_is_rewritten_and_retrieves_same_document(config):
    ollama = FakeOllama(chat_reply=lambda body: json.dumps(
        {"question": "What is the daily allowance in cities that are not metro cities?"}))
    rig = Rig(config, ollama)
    try:
        rig.load(*TEXT_DOCS)
        first = rig.ask("What is the daily allowance in metro cities?", session_id="s1")
        rig.env.db.add_message("s1", "user", "What is the daily allowance in metro cities?")
        rig.env.db.add_message("s1", "assistant", "It is INR 2,500 in metro cities.")
        follow = rig.ask("What about other cities?", session_id="s1")
        assert follow.rewritten and follow.standalone_question.startswith("What is the daily allowance")
        assert follow.steps[0].title == "Using the conversation"
        assert follow.passages[0].row["doc_name"] == first.passages[0].row["doc_name"] == "employee_handbook.pdf"
        prompt = rig.chat_calls()[0]["messages"][0]["content"]
        assert "User: What is the daily allowance in metro cities?" in prompt and "What about other cities?" in prompt
        assert rig.chat_calls()[0]["options"]["num_ctx"] == config.ollama.num_ctx
    finally:
        rig.close()


def test_follow_up_rewrite_falls_back_when_model_fails(config):
    rig = Rig(config, FakeOllama(chat_reply=lambda body: "not json"))
    try:
        rig.load(*TEXT_DOCS)
        rig.env.db.add_message("s1", "user", "What is the daily allowance in metro cities?")
        res = rig.ask("What about other cities?", session_id="s1")
        assert res.standalone_question == "What is the daily allowance in metro cities? What about other cities?"
        assert res.passages[0].row["doc_name"] == "employee_handbook.pdf"
    finally:
        rig.close()


def test_standalone_question_is_not_rewritten(rig):
    rig.load(*TEXT_DOCS)
    rig.env.db.add_message("s1", "user", "What is the daily allowance in metro cities?")
    res = rig.ask("What is the notice period for head-office staff?", session_id="s1")
    assert not res.rewritten and rig.chat_calls() == []


def test_history_survives_restart_and_respects_history_turns(tmp_path):
    cfg = make_config(tmp_path, retrieval={"history_turns": 2})
    env = Env(cfg)
    for i in range(4):
        env.db.add_message("s", "user", f"q{i}")
        env.db.add_message("s", "assistant", f"a{i}")
    env.close()
    env = Env(cfg)  # "restart"
    assert env.db.recent_turns("s", cfg.retrieval.history_turns) == [("q2", "a2"), ("q3", "a3")]
    env.close()


# -- AC: with the reranker off, unanswerable questions are refused via vector_threshold -----


def test_reranker_off_refuses_through_vector_threshold(tmp_path):
    rig = Rig(make_config(tmp_path, reranker={"enabled": False}), reranker=False)
    try:
        rig.load(*TEXT_DOCS)
        res = rig.ask("What is the capital of France?")
        assert res.gate.method == "vector" and res.gate.threshold == 0.55
        assert res.gate.passed is False and rig.chat_calls() == []
        assert all(c.rerank_score is None for c in res.candidates)
    finally:
        rig.close()


def test_reranker_off_answerable_passes_when_similar_enough(tmp_path):
    # A low vector_threshold here only exercises the mechanism with the fake embedder.
    rig = Rig(make_config(tmp_path, reranker={"enabled": False}, retrieval={"vector_threshold": 0.2}), reranker=False)
    try:
        rig.load(*TEXT_DOCS)
        res = rig.ask("daily allowance metro cities")
        assert res.gate.passed and res.gate.best_score >= 0.2
    finally:
        rig.close()


def test_enabled_reranker_that_failed_to_load_is_an_error(config):
    rig = Rig(config, reranker=False)
    try:
        rig.load("employee_handbook.pdf")
        with pytest.raises(RetrievalError, match="did not load"):
            rig.ask("What is the daily allowance?")
    finally:
        rig.close()


# -- document scope: only ticked documents are searched ------------------------------------


def test_unticked_document_is_not_searched(rig):
    docs = rig.load(*TEXT_DOCS)
    only = [docs["supplier_code_of_conduct.docx"]["document_id"]]
    res = rig.ask("What is the daily allowance in metro cities?", document_ids=only)
    assert {c.row["doc_name"] for c in res.candidates} <= {"supplier_code_of_conduct.docx"}
    assert "employee_handbook.pdf" in res.excluded_documents
    assert res.steps[1].detail["excluded_documents"] == res.excluded_documents
    assert res.gate.passed is False


def test_no_documents_ticked_is_refused_without_search(rig):
    rig.load(*TEXT_DOCS)
    res = rig.ask("What is the daily allowance?", document_ids=[])
    assert res.candidates == [] and res.gate.passed is False and rig.chat_calls() == []


# -- routing -----------------------------------------------------------------------------------


def test_routing_rules(rig):
    rig.load("sales_q3.csv", department="Finance")
    schemas = rig.env.db.active_table_schemas()
    calc = route_by_rules("Which region had the highest Sales in August?", schemas)
    assert calc.route == "calculation" and "highest" in calc.keywords and set(calc.columns) >= {"Region", "Sales"}
    assert route_by_rules("What is the refund window?", schemas).route == "lookup"
    assert route_by_rules("What is the refund window?", []).route == "lookup"
    assert route_by_rules("How many days of annual leave do I get?", schemas) is None  # inconclusive


def test_calculation_route_stops_text_pipeline_without_llm(rig):
    rig.load("sales_q3.csv", department="Finance")
    res = rig.ask("What were the total Units sold per Product?")
    assert res.route.route == "calculation" and res.route.tables and rig.chat_calls() == []
    assert res.candidates == [] and res.steps[-1].title == "Understanding the question"


def test_inconclusive_routing_asks_the_model_for_one_label(config):
    rig = Rig(config, FakeOllama(chat_reply=lambda body: json.dumps({"label": "lookup"})))
    try:
        rig.load("sales_q3.csv", department="Finance")
        rig.load("employee_handbook.pdf")
        res = rig.ask("How many days of annual leave do I get?")
        assert res.route.used_llm and res.route.route == "lookup"
        [call] = rig.chat_calls()
        assert call["format"]["properties"]["label"]["enum"] == ["lookup", "calculation"]
        assert res.llm_calls == [{"purpose": "route", "model": "qwen2.5:7b-instruct", "ms": res.llm_calls[0]["ms"],
                                  "ok": True}]
    finally:
        rig.close()


def test_routing_ignores_tables_of_unticked_documents(rig):
    docs = rig.load("sales_q3.csv", department="Finance")
    rig.load("employee_handbook.pdf")
    handbook = rig.env.db.one("SELECT id FROM documents WHERE name = 'employee_handbook.pdf'")["id"]
    res = rig.ask("Which region had the highest Sales?", document_ids=[handbook])
    assert res.route.route == "lookup" and docs


# -- timing ----------------------------------------------------------------------------------


def test_retrieval_time_is_recorded(rig):
    rig.load(*TEXT_DOCS)
    res = rig.ask("What is the daily allowance in metro cities?")
    assert res.retrieval_ms >= 0
    titles = [s.title for s in res.steps]
    assert titles == ["Understanding the question", "Finding candidate passages", "Ranking by relevance",
                      "Is the answer in the documents?"]
