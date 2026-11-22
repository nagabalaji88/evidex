"""Spec section 5 table-path acceptance criteria and SQL safety."""

from __future__ import annotations

import csv

import pytest

from app.answer.schema import REFUSAL_PHRASE
from app.sqlpath.sql import NO_ROWS_MESSAGE, SqlRejected, run_sql, validate_sql

from .conftest import SAMPLE_DOCS, ScriptedLLM, make_config
from .test_answering import AskRig

SALES_Q = "What is the total Sales for Region South?"


def expected_south_sales() -> int:
    with (SAMPLE_DOCS / "sales_q3.csv").open() as fh:
        return sum(int(r["Sales"]) for r in csv.DictReader(fh) if r["Region"] == "South")


@pytest.fixture
def rig(config):
    rigs = []

    def make(script, docs=("sales_q3.csv", "employee_handbook.pdf"), cfg=None):
        r = AskRig(cfg or config, script, list(docs))
        rigs.append(r)
        return r

    yield make
    for r in rigs:
        r.close()


def table(rig, name="sales_q3.csv"):
    return next(s for s in rig.env.db.active_table_schemas() if s["file"] == name)


def sum_sql(prompt):
    import re
    name = re.search(r'Table "(tbl_[^"]+)"', prompt).group(1)
    return {"sql": f'SELECT SUM("Sales") AS total_sales FROM "{name}" WHERE "Region" = \'South\''}


# -- AC: a calculation over a CSV returns the correct number plus the SQL used ------------


def test_calculation_returns_correct_number_and_sql(rig):
    expected = expected_south_sales()
    r = rig(ScriptedLLM(sql=sum_sql, sql_answer=lambda p: {"answer": f"The total Sales for South is {expected:,}."}))
    ev, events = r.ask(SALES_Q)
    a = ev.data
    assert a["kind"] == "calculation" and a["confidence"]["level"] == "Exact"
    assert a["sql"]["rows"] == [[expected]] and a["sql"]["columns"] == ["total_sales"]
    assert a["sql"]["query"].startswith('SELECT SUM("Sales")') and a["sql"]["file"] == "sales_q3.csv"
    assert a["answer"] == f"The total Sales for South is {expected:,}."
    steps = [e.data["data"]["step"]["title"] for e in events if e.event == "stage" and e.data["status"] == "done"]
    assert steps == ["Understanding the question", "Picking the table", "Writing the query", "Safety checks",
                     "Running the query", "Checking the number", "Answer ready"]  # 7 steps
    prompt = r.script.prompts["sql"][0]
    assert '"Region" TEXT' in prompt and "Sample rows:" in prompt and prompt.count("Question:") == 4


def test_answer_number_must_match_result_exactly(rig):
    expected = expected_south_sales()
    r = rig(ScriptedLLM(sql=sum_sql, sql_answer={"answer": "The total Sales for South is about 1 crore."}))
    ev, _ = r.ask(SALES_Q)
    assert ev.data["answer"] == f"total_sales: {expected}."  # written by code from the result instead


# -- AC: SQL that writes, deletes or names a non-existent column is blocked ----------------


@pytest.mark.parametrize("sql,check", [
    ('DELETE FROM "{t}"', "One statement"),
    ('UPDATE "{t}" SET "Sales" = 0', "One statement"),
    ('SELECT 1; DROP TABLE "{t}"', "One statement"),
    ('SELECT * FROM "{t}" WHERE 1 = (SELECT 1 FROM pragma_table_info(\'x\'))', "Real names"),
    ('SELECT "Profit" FROM "{t}"', "Real names"),
    ('SELECT * FROM chunks', "Real names"),
    ("SELECT sqlite_version() UNION SELECT name FROM sqlite_master", "Real names"),
    ('SELECT * FROM "{t}" WHERE "Product" = \'x\' AND 0 IN (SELECT 1 FROM documents)', "Real names"),
    ("ATTACH DATABASE 'x.db' AS x", "One statement"),
    ('SELECT * FROM "{t}" /* PRAGMA */ WHERE 1', None),  # keyword only inside a comment: fine
])
def test_unsafe_sql_is_blocked(rig, sql, check):
    r = rig(ScriptedLLM())
    s = table(r)
    sql = sql.format(t=s["table_name"])
    if check is None:
        validate_sql(sql, [s], r.env.db.path)
        return
    with pytest.raises(SqlRejected) as exc:
        validate_sql(sql, [s], r.env.db.path)
    assert exc.value.check == check


def test_blocked_keyword_names_the_problem(rig):
    r = rig(ScriptedLLM())
    s = table(r)
    with pytest.raises(SqlRejected, match="PRAGMA is not allowed"):
        validate_sql(f'SELECT * FROM "{s["table_name"]}" WHERE pragma = 1', [s], r.env.db.path)


def test_bad_sql_is_retried_once_then_refused(rig):
    r = rig(ScriptedLLM(sql={"sql": "DELETE FROM x"}))
    ev, _ = r.ask(SALES_Q)
    assert ev.data["kind"] == "refusal" and ev.data["answer"] == REFUSAL_PHRASE
    assert r.script.count("sql") == 2
    assert "rejected" in r.script.prompts["sql"][1]
    assert table(r)["row_count"] == r.env.db.one(f'SELECT COUNT(*) AS n FROM "{table(r)["table_name"]}"')["n"]


def test_bad_sql_then_good_sql_succeeds(rig):
    r = rig(ScriptedLLM(sql=[{"sql": 'SELECT "Profit" FROM x'}, sum_sql],
                        sql_answer=lambda p: {"answer": f"South sold {expected_south_sales()}."}))
    ev, _ = r.ask(SALES_Q)
    assert ev.data["kind"] == "calculation" and ev.data["sql"]["rows"] == [[expected_south_sales()]]


def test_database_is_never_modified_by_generated_sql(rig):
    r = rig(ScriptedLLM())
    s = table(r)
    with pytest.raises(Exception):
        run_sql(f'DELETE FROM "{s["table_name"]}"', r.env.db.path, timeout_seconds=5, max_rows=10)
    assert r.env.db.one(f'SELECT COUNT(*) AS n FROM "{s["table_name"]}"')["n"] == 60


# -- AC: a calculation question with no matching table returns the refusal ------------------


def test_calculation_without_matching_table_is_refused(rig):
    r = rig(ScriptedLLM(route={"label": "calculation"}))
    ev, _ = r.ask("What is the average Salary per Department?")  # rules unsure, model says calculation
    assert ev.data["kind"] == "refusal" and ev.data["answer"] == REFUSAL_PHRASE
    assert r.script.count("sql") == 0


def test_calculation_on_unticked_table_falls_back_to_text(rig):
    r = rig(ScriptedLLM())
    handbook = r.env.db.one("SELECT id FROM documents WHERE name = 'employee_handbook.pdf'")["id"]
    ev, _ = r.ask("What is the total Sales for Region South?", document_ids=[handbook])
    assert ev.data["kind"] == "refusal" and r.script.count("sql") == 0


# -- empty result, limits -------------------------------------------------------------------


def test_empty_result_says_no_rows_and_shows_sql(rig):
    def none_sql(prompt):
        q = sum_sql(prompt)["sql"].replace("'South'", "'Antarctica'")
        return {"sql": q.replace("SUM(\"Sales\") AS total_sales", "\"Product\"")}
    r = rig(ScriptedLLM(sql=none_sql))
    ev, _ = r.ask(SALES_Q)
    assert ev.data["answer"] == NO_ROWS_MESSAGE and "Antarctica" in ev.data["sql"]["query"]
    assert ev.data["sql"]["rows"] == []


def test_row_limit_and_timeout(rig, tmp_path):
    r = rig(ScriptedLLM())
    t = table(r)["table_name"]
    out = run_sql(f'SELECT * FROM "{t}"', r.env.db.path, timeout_seconds=5, max_rows=10)
    assert len(out.rows) == 10 and out.truncated
    with pytest.raises(SqlRejected) as exc:
        run_sql(f'SELECT COUNT(*) FROM "{t}" a, "{t}" b, "{t}" c, "{t}" d, "{t}" e', r.env.db.path,
                timeout_seconds=0.05, max_rows=10)
    assert exc.value.check == "Limits"


def test_limits_come_from_config(tmp_path, rig):
    cfg = make_config(tmp_path, sql={"max_rows": 5})
    def all_rows(prompt):
        import re
        return {"sql": f'SELECT * FROM "{re.search(r"Table \"(tbl_[^\"]+)\"", prompt).group(1)}"'}
    r = rig(ScriptedLLM(sql=all_rows, sql_answer={"answer": "Rows are shown."}), cfg=cfg)
    ev, _ = r.ask("Show the top Sales rows ranked by Region")
    assert len(ev.data["sql"]["rows"]) == 5 and ev.data["sql"]["truncated"]
