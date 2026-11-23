"""POST /api/ask orchestration: retrieval (section 5) -> answer (section 3) -> grounding
(section 6), or the table path (section 5), streamed as events (section 8.1).

Ask stages: searching -> ranking -> writing -> checking. Each finished step is also sent
as a `stage` event (status done) carrying the step, for the "Behind the scenes" panel.
Questions run one at a time.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from ..config import Config
from ..grounding.checks import (GroundingReport, apply_claim_result, citation_check, clean_text, number_name_check,
                                summarise)
from ..grounding.normalise import find_names, numbers_and_dates
from ..ingestion.pipeline import Event
from ..logs.questions import log_question
from ..ollama_client import OllamaClient, OllamaError
from ..prompts import render_prompt
from ..retrieval.retriever import RetrievalError, RetrievalResult, Retriever, Step
from ..retrieval.route import RouteDecision
from ..sqlpath.sql import NO_ROWS_MESSAGE, SqlRejected, describe_tables, run_sql, validate_sql
from ..store.sqlite import Database, now_iso
from .schema import ANSWER_SCHEMA, REFUSAL_PHRASE
from .sentences import citation_for, number_sentences

INVALID_RESPONSE = "The model returned an invalid response. Please try again."
PARTIAL_SUFFIX = "The rest of this is not available in the provided documents."
SQL_SCHEMA = {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}
SQL_ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
CLAIM_SCHEMA = {"type": "object", "properties": {"supported": {"type": "boolean"},
                                                 "unsupported_sentences": {"type": "array", "items": {"type": "string"}}},
                "required": ["supported", "unsupported_sentences"]}

_STAGE_OF = {
    "Using the conversation": "searching", "Understanding the question": "searching",
    "Finding candidate passages": "searching", "Picking the table": "searching",
    "Ranking by relevance": "ranking", "Is the answer in the documents?": "ranking",
    "Writing the answer": "writing", "Writing the query": "writing", "Safety checks": "checking",
    "Running the query": "checking", "Checking the evidence": "checking", "Checking the number": "checking",
    "Answer ready": "checking",
}


def confidence_level(score: float, config: Config) -> str:
    c = config.confidence
    return "High" if score >= c.high_from else "Medium" if score >= c.medium_from else "Low"


class AskError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class AskService:
    def __init__(self, config: Config, db: Database, retriever: Retriever, llm: OllamaClient):
        self.config, self.db, self.retriever, self.llm = config, db, retriever, llm
        self._lock = asyncio.Lock()  # one question at a time (spec section 11)
        db.conn.executescript(
            "CREATE TABLE IF NOT EXISTS grounding_failures (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, "
            "question TEXT NOT NULL, chunks TEXT NOT NULL, answer TEXT NOT NULL, failed_check TEXT NOT NULL, "
            "detail TEXT NOT NULL, attempt INTEGER NOT NULL, question_log_id INTEGER)")

    async def ask(self, question: str, *, session_id: str,
                  document_ids: list[int] | None = None) -> AsyncIterator[Event]:
        """Models come from Settings only: the answer model, and per-task models (config.model_for)."""
        async with self._lock:
            run = _Run(self, question.strip(), session_id, document_ids, self.config.models.answer)
            async for ev in run.go():
                yield ev


class _Run:
    def __init__(self, svc: AskService, question: str, session_id: str, document_ids, model: str):
        self.svc, self.cfg, self.db = svc, svc.config, svc.db
        self.question, self.session_id, self.document_ids, self.model = question, session_id, document_ids, model
        self.t0 = time.perf_counter()
        self.steps: list[Step] = []
        self.answer_llm_called = False
        self.tokens: list[dict[str, Any]] = []
        self.raw_outputs: list[str] = []
        self.failures: list[tuple[str, str, int]] = []  # (check, detail, attempt)
        self.result: RetrievalResult | None = None

    # -- plumbing ---------------------------------------------------------------------------

    def _step(self, step: Step) -> Event:
        self.steps.append(step)
        return Event("stage", {"stage": _STAGE_OF.get(step.title, "checking"), "detail": step.summary,
                               "status": "done", "data": {"step": step.as_dict()}})

    def _stage(self, stage: str, detail: str) -> Event:
        return Event("stage", {"stage": stage, "detail": detail, "status": "running"})

    def _where(self) -> str:
        return "Google Gemini API" if self.cfg.uses_gemini else "running locally"

    def _elapsed(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)

    async def _chat(self, purpose: str, prompt: str, schema: dict) -> tuple[dict | None, str]:
        """One call; returns (parsed JSON or None, raw text). Ollama errors propagate.
        "sql" and "claim" may have their own model in Settings; the rest use the answer model."""
        out = await self.svc.llm.chat([{"role": "user", "content": prompt}], schema=schema,
                                      model=self.cfg.model_for(purpose))
        self.tokens.append({"purpose": purpose, "prompt_tokens": out.prompt_tokens, "output_tokens": out.output_tokens,
                            "ms": int(out.total_ms)})
        try:
            data = json.loads(out.content)
        except (json.JSONDecodeError, TypeError):
            data = None
        return (data if isinstance(data, dict) else None), out.content

    def _base_payload(self, **kw) -> dict[str, Any]:
        r = self.result
        return {"ai_called": self.answer_llm_called, "model": self.model,
                "not_evaluated": self.model != self.cfg.models.answer, "question": self.question,
                "standalone_question": r.standalone_question if r else self.question, **kw}

    def _finish(self, payload: dict[str, Any], outcome: str, extra_log: dict | None = None) -> Event:
        payload["elapsed_ms"] = self._elapsed()
        log_id = log_question(
            self.db, self.result, session_id=self.session_id, outcome=outcome,
            answer_llm_called=self.answer_llm_called, steps=[s.as_dict() for s in self.steps],
            model=self.model, answer=payload["answer"], answer_json=payload,
            confidence=(payload.get("confidence") or {}).get("score"),
            confidence_level=(payload.get("confidence") or {}).get("level"),
            checks=payload.get("checks", []), total_ms=payload["elapsed_ms"], tokens=self.tokens,
            cited_documents=sorted({e["doc_name"] for e in payload.get("evidence", [])}), **(extra_log or {}))
        for check, detail, attempt in self.failures:
            self.db.conn.execute(
                "INSERT INTO grounding_failures(created_at, question, chunks, answer, failed_check, detail, attempt, "
                "question_log_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), self.question, json.dumps([c.summary() | {"text": c.row["text"]}
                                                       for c in self.result.passages]),
                 json.dumps(self.raw_outputs), check, detail, attempt, log_id))
        payload["trace_id"] = log_id
        if self.session_id:
            self.db.add_message(self.session_id, "user", self.question)
            self.db.add_message(self.session_id, "assistant", payload["answer"])
        return Event("answer", payload)

    def _error(self, code: str, message: str) -> Event:
        if self.result is not None:
            log_question(self.db, self.result, session_id=self.session_id, outcome="error",
                         answer_llm_called=self.answer_llm_called, steps=[s.as_dict() for s in self.steps],
                         model=self.model, error=f"{code}: {message}", total_ms=self._elapsed(), tokens=self.tokens,
                         answer=None)
        else:
            empty = RetrievalResult(self.question, self.question, False, RouteDecision("lookup", ""), self.document_ids)
            log_question(self.db, empty, session_id=self.session_id, outcome="error", model=self.model,
                         error=f"{code}: {message}", total_ms=self._elapsed(), answer=None)
        return Event("error", {"code": code, "message": message})

    def _skip_rest(self, titles: list[str]) -> list[Event]:
        return [self._step(Step(t, "", "skipped", "Not needed. The agent stopped before this step.")) for t in titles]

    # -- main flow --------------------------------------------------------------------------

    async def go(self) -> AsyncIterator[Event]:
        if not self.question:
            yield Event("error", {"code": "empty_question", "message": "Type a question first."})
            return
        yield self._stage("searching", "Searching the documents")
        try:
            self.result = await self.svc.retriever.retrieve(
                self.question, session_id=self.session_id, document_ids=self.document_ids)
        except RetrievalError as exc:
            yield self._error(exc.code, exc.message)
            return
        except OllamaError as exc:
            yield self._error("ollama_unavailable", f"The model server is not available: {exc}")
            return
        for s in self.result.steps:
            if s.title == "Ranking by relevance":
                yield self._stage("ranking", "Ranking by relevance")
            yield self._step(s)
        try:
            if self.result.route.route == "calculation":
                gen = self._table_path()
            else:
                gen = self._text_path()
            async for ev in gen:
                yield ev
        except OllamaError as exc:
            yield self._error("ollama_unavailable", f"The model server is not available: {exc}")
        except AskError as exc:
            yield self._error(exc.code, exc.message)

    # -- text path ----------------------------------------------------------------------------

    def _refusal(self, *, reason: str, outcome: str = "not_in_documents") -> Event:
        g = self.result.gate
        closest = None
        if g is not None and g.best_chunk:
            b = g.best_chunk
            closest = {"doc_name": b["doc_name"], "version": b["version"], "page": b["page"], "section": b["section"],
                       "source_location": b["source_location"], "score": g.best_score, "threshold": g.threshold}
        payload = self._base_payload(kind="refusal", found_in_document=False, answer=REFUSAL_PHRASE, closest=closest,
                                     missing=reason)
        return self._finish(payload, outcome)

    async def _text_path(self) -> AsyncIterator[Event]:
        r = self.result
        if not r.gate.passed:
            for ev in self._skip_rest(["Writing the answer", "Checking the evidence", "Answer ready"]):
                yield ev
            yield self._refusal(reason="Nothing in the selected documents is relevant enough to answer.")
            return

        yield self._stage("writing", "Writing the answer")
        numbered, smap = number_sentences(r.passages)
        source_text = "\n".join(p.row["text"] for p in r.passages)
        retry_note = ""
        attempts = 1 + self.cfg.grounding.max_retries
        report: GroundingReport | None = None
        data: dict | None = None
        for attempt in range(1, attempts + 1):
            t = time.perf_counter()
            prompt = render_prompt("answer_prompt.md", numbered_sentences=numbered, user_question=r.standalone_question,
                                   retry_note=retry_note)
            data = await self._generate(prompt)
            self.steps_write(attempt, t, data, len(r.passages))
            yield self._step_event_last()
            if not data.get("found_in_document"):
                for ev in self._skip_rest(["Checking the evidence"]):
                    yield ev
                yield self._step(Step("Answer ready", "done", "done", "The model found no answer in the passages"))
                yield self._refusal(reason=str(data.get("missing") or ""), outcome="not_in_documents")
                return
            yield self._stage("checking", "Checking the evidence")
            t = time.perf_counter()
            try:
                report = await self._ground(data, smap, source_text)
            except OllamaError:
                raise
            except Exception as exc:  # a crash inside the checker is an error state, not a refusal
                raise AskError("grounding_error", f"The grounding check failed to run: {exc}") from exc
            ok = report.all_passed
            for v in report.sentences:
                for f in v.failures:
                    self.failures.append((f["check"], f"\"{v.text}\": {f['detail']}", attempt))
            yield self._step(Step("Checking the evidence", "grounding check", "done" if ok else "failed",
                                  f"{sum(c['passed'] for c in report.checks)} of {len(report.checks)} checks passed"
                                  + ("" if ok or attempt == attempts else " · retrying with a stricter instruction"),
                                  {"checks": report.checks, "attempt": attempt,
                                   "failures": report.failure_summary()}, _ms(t)))
            if ok:
                break
            retry_note = ("\nYour previous answer failed these checks. Write it again using only the source "
                          "sentences, citing a real ID for every sentence:\n- " + "\n- ".join(report.failure_summary())
                          + "\n")
        passed = report.passed
        partial = not report.all_passed
        if not passed:
            yield self._step(Step("Answer ready", "done", "blocked", "No sentence passed the checks"))
            yield self._refusal(reason="The answer could not be supported by the documents.", outcome="refused_grounding")
            return

        payload = self._text_payload(passed, smap, report, partial, str(data.get("missing") or ""))
        conf = payload["confidence"]
        yield self._step(Step("Answer ready", "done", "done", f"{conf['level']} · {conf['score']:.2f}",
                              {"confidence": conf}))
        yield self._finish(payload, "partial" if partial else "answered")

    def steps_write(self, attempt: int, t: float, data: dict, n_passages: int) -> None:
        sentences = data.get("sentences") or []
        self._pending = Step(
            "Writing the answer", f"{self.model} · {self._where()}", "done",
            f"Used {n_passages} passages and tagged every sentence" if attempt == 1 else f"Attempt {attempt}",
            {"raw": self.raw_outputs[-1] if self.raw_outputs else "", "attempt": attempt,
             "sentences": len(sentences)}, _ms(t))

    def _step_event_last(self) -> Event:
        return self._step(self._pending)

    async def _generate(self, prompt: str) -> dict:
        """Answer call with the schema in `format`. Parse failure: retry once, then error state."""
        for _ in range(2):
            self.answer_llm_called = True
            data, raw = await self._chat("answer", prompt, ANSWER_SCHEMA)
            self.raw_outputs.append(raw)
            if data is not None and isinstance(data.get("found_in_document"), bool) and \
                    isinstance(data.get("sentences", []), list):
                return data
        raise AskError("invalid_response", INVALID_RESPONSE)

    async def _ground(self, data: dict, smap, source_text: str) -> GroundingReport:
        sentences = [s for s in data.get("sentences") or [] if isinstance(s, dict)]
        verdicts = citation_check(sentences, smap)
        number_name_check(verdicts, source_text)
        claim_ran = False
        if self.cfg.grounding.claim_check_enabled and any(v.passed for v in verdicts):
            excerpts = "\n".join(f"[{k}] {s.text}" for k, s in smap.items())
            listed = "\n".join(f"- {v.text}" for v in verdicts if v.passed)
            out, _ = await self._chat("claim", render_prompt("claim_prompt.md", excerpts=excerpts, sentences=listed),
                                      CLAIM_SCHEMA)
            apply_claim_result(verdicts, out)
            claim_ran = True
        if not verdicts:
            from ..grounding.checks import SentenceVerdict
            v = SentenceVerdict("", [])
            v.fail("citation", "the model returned no sentences")
            verdicts = [v]
        return GroundingReport(verdicts, summarise(verdicts, smap, claim_ran))

    def _text_payload(self, passed, smap, report: GroundingReport, partial: bool, missing: str) -> dict[str, Any]:
        refs: dict[str, int] = {}
        evidence: dict[str, dict[str, Any]] = {}
        out_sentences = []
        by_chunk = {p.chunk_id: p for p in self.result.passages}
        for v in passed:
            cites = [citation_for(smap[i]) for i in v.ids]
            for c in cites:
                if c["chunk_id"] not in refs:
                    refs[c["chunk_id"]] = len(refs) + 1
                    p = by_chunk[c["chunk_id"]]
                    evidence[c["chunk_id"]] = {
                        "ref": refs[c["chunk_id"]], "chunk_id": c["chunk_id"], "document_id": c["document_id"],
                        "doc_name": c["doc_name"], "version": c["version"], "page": c["page"], "section": c["section"],
                        "source_location": c["source_location"], "chunk_text": p.row["body"], "highlights": [],
                        "score": round(p.score, 4)}
                if c["quote"] not in evidence[c["chunk_id"]]["highlights"]:
                    evidence[c["chunk_id"]]["highlights"].append(c["quote"])
            out_sentences.append({"text": v.text, "ids": v.ids, "refs": sorted({refs[c["chunk_id"]] for c in cites}),
                                  "citations": cites})
        text = " ".join(s["text"] for s in out_sentences)
        if partial:
            text = f"{text} {PARTIAL_SUFFIX}"
        best = max(e["score"] for e in evidence.values())
        level = confidence_level(best, self.cfg)
        g = self.result.gate
        c = self.cfg.confidence
        reason = {"High": "Strong match.", "Medium": "Good match, but check the evidence.",
                  "Low": "Weak match: read the evidence before relying on it."}[level]
        return self._base_payload(
            kind="text", found_in_document=True, answer=text, sentences=out_sentences, missing=missing,
            partial=partial, evidence=sorted(evidence.values(), key=lambda e: e["ref"]),
            confidence={"level": level, "score": best, "threshold": g.threshold, "high_from": c.high_from,
                        "medium_from": c.medium_from,
                        "reason": f"{reason} The best cited passage scores {best:.2f} "
                                  f"({'reranker' if g.method == 'reranker' else 'vector similarity'})."},
            checks=report.checks)

    # -- table path ---------------------------------------------------------------------------

    async def _table_path(self) -> AsyncIterator[Event]:
        r = self.result
        schemas = [s for s in self.db.active_table_schemas() if s["table_name"] in set(r.route.tables)]
        later = ["Writing the query", "Safety checks", "Running the query", "Checking the number", "Answer ready"]
        t = time.perf_counter()
        matched_cols = set(r.route.columns)
        schemas = [s for s in schemas if any(c["name"] in matched_cols for c in s["columns"])]
        if not schemas:
            yield self._step(Step("Picking the table", "schema match", "blocked", "No table has the columns needed",
                                  {}, _ms(t)))
            for ev in self._skip_rest(later):
                yield ev
            yield self._refusal(reason="No table in the selected documents has the columns this question needs.")
            return
        yield self._step(Step(
            "Picking the table", "schema match", "done",
            " · ".join(f"{s['file']}{' · ' + s['sheet'] if s['sheet'] else ''} · {s['row_count']:,} rows"
                       for s in schemas),
            {"tables": [{"table": s["table_name"], "file": s["file"], "sheet": s["sheet"], "rows": s["row_count"],
                         "columns": [c["name"] for c in s["columns"]], "matched": sorted(matched_cols)}
                        for s in schemas]}, _ms(t)))

        yield self._stage("writing", "Writing the query")
        conn = self.db.conn
        tables_desc = describe_tables(conn, schemas)
        retry_note, run, last_error = "", None, ""
        for attempt in (1, 2):  # spec: on failure, retry once, then refuse
            t = time.perf_counter()
            prompt = render_prompt("sql_prompt.md", tables=tables_desc, user_question=r.standalone_question,
                                   retry_note=retry_note)
            self.answer_llm_called = True
            data, raw = await self._chat("sql", prompt, SQL_SCHEMA)
            self.raw_outputs.append(raw)
            sql = str((data or {}).get("sql") or "").strip()
            yield self._step(Step("Writing the query", f"{self.cfg.model_for('sql')} · {self._where()}",
                                  "done" if sql else "failed",
                                  "One read-only SELECT" if sql else "The model wrote no query",
                                  {"sql": sql, "attempt": attempt, "raw": raw}, _ms(t)))
            t = time.perf_counter()
            try:
                sql, checks = validate_sql(sql, schemas, self.db.path)
                checks.append({"name": "Limits", "passed": True,
                               "detail": f"Read-only connection, {self.cfg.sql.timeout_seconds:g} s timeout, "
                                         f"{self.cfg.sql.max_rows} rows max"})
                yield self._step(Step("Safety checks", "SQL validation", "done",
                                      f"{len(checks)} of {len(checks)} checks passed", {"checks": checks}, _ms(t)))
                t = time.perf_counter()
                run = run_sql(sql, self.db.path, timeout_seconds=self.cfg.sql.timeout_seconds,
                              max_rows=self.cfg.sql.max_rows)
                run.checks = checks
                yield self._step(Step("Running the query", "SQLite · read-only", "done",
                                      f"{len(run.rows)} row{'s' if len(run.rows) != 1 else ''} in {run.ms} ms",
                                      {"columns": run.columns, "rows": run.rows[:20], "truncated": run.truncated},
                                      _ms(t)))
                break
            except SqlRejected as exc:
                last_error = f"{exc.check}: {exc.message}"
                self.failures.append(("sql", last_error, attempt))
                title = "Running the query" if exc.check in ("Run", "Limits") else "Safety checks"
                yield self._step(Step(title, "SQL validation", "failed", exc.message,
                                      {"failed_check": exc.check, "sql": sql}, _ms(t)))
                retry_note = f"\nYour previous query was rejected ({last_error}). Write a corrected query.\n"
                run = None
        if run is None:
            for ev in self._skip_rest(["Checking the number", "Answer ready"]):
                yield ev
            yield self._refusal(reason=f"A safe query could not be written ({last_error}).", outcome="not_in_documents")
            return

        schema = schemas[0] if len(schemas) == 1 else next(
            (s for s in schemas if quote_in(s["table_name"], run.sql)), schemas[0])
        ver = self.db.one("SELECT version FROM versions WHERE id = ?", (schema["version_id"],))["version"]
        sql_payload = {"query": run.sql, "columns": run.columns, "rows": run.rows, "truncated": run.truncated,
                       "table": schema["table_name"], "file": schema["file"], "sheet": schema["sheet"], "version": ver,
                       "row_count": schema["row_count"], "ms": run.ms}
        evidence = [{"ref": 1, "chunk_id": "", "document_id": schema["document_id"], "doc_name": schema["file"],
                     "version": ver, "page": None, "section": schema["sheet"],
                     "source_location": (f"sheet {schema['sheet']}, " if schema["sheet"] else "")
                     + f"{schema['row_count']:,} rows",
                     "chunk_text": run.sql, "highlights": [], "score": 1.0}]
        base_checks = [{"name": "Read-only query", "passed": True, "detail": "A single SELECT on a read-only connection"},
                       {"name": "Columns exist", "passed": True, "detail": "Every table and column is real"}]

        if not run.rows:
            yield self._step(Step("Checking the number", "grounding check", "done", "No rows to check"))
            yield self._step(Step("Answer ready", "done", "done", "Exact: no rows matched"))
            payload = self._base_payload(kind="calculation", found_in_document=True, answer=NO_ROWS_MESSAGE,
                                         sql=sql_payload, evidence=evidence, checks=base_checks,
                                         confidence={"level": "Exact", "reason": "Calculated from the data."})
            yield self._finish(payload, "answered")
            return

        t = time.perf_counter()
        answer, check = await self._phrase_sql_answer(run)
        yield self._step(Step("Checking the number", "grounding check", "done", check["detail"], {"checks": [check]},
                              _ms(t)))
        yield self._step(Step("Answer ready", "done", "done", "Exact: calculated from the data"))
        payload = self._base_payload(
            kind="calculation", found_in_document=True, answer=answer, sql=sql_payload, evidence=evidence,
            checks=base_checks + [{"name": "Number matches result", "passed": True, "detail": check["detail"]}],
            confidence={"level": "Exact", "reason": "Calculated from the spreadsheet, not written by the AI, so it has "
                                                    "no confidence score."})
        yield self._finish(payload, "answered")

    async def _phrase_sql_answer(self, run) -> tuple[str, dict[str, Any]]:
        """The model words the answer; every number in it must equal a value in the result
        (or a value from the question or SQL). Otherwise the answer is written by code."""
        rows_preview = run.rows[:20]
        prompt = render_prompt("sql_answer_prompt.md", user_question=self.result.standalone_question, sql=run.sql,
                               columns=json.dumps(run.columns), rows=json.dumps(rows_preview, default=str))
        data, raw = await self._chat("sql_answer", prompt, SQL_ANSWER_SCHEMA)
        self.raw_outputs.append(raw)
        text = clean_text(str((data or {}).get("answer") or ""))
        allowed_text = " ".join(str(v) for row in run.rows for v in row) + " " + " ".join(run.columns)
        allowed_nums, allowed_dates = numbers_and_dates(allowed_text + " " + self.result.standalone_question + " " +
                                                        run.sql)
        # result cells are exact values: add them directly too (e.g. floats)
        for row in run.rows:
            for v in row:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    from ..grounding.normalise import normalise_number
                    n = normalise_number(repr(float(v)) if isinstance(v, float) else str(v))
                    if n:
                        allowed_nums.add(n)
        if text:
            nums, dates = numbers_and_dates(text)
            bad = sorted(n for n in nums if n not in allowed_nums) + sorted(d for d in dates if d not in allowed_dates)
            names_src = allowed_text + " " + self.result.standalone_question
            bad_names = [n for n in find_names(text) if n.lower() not in names_src.lower()]
            if not bad and not bad_names:
                shown = ", ".join(sorted(nums)) or "no numbers"
                return text, {"name": "Number", "passed": True, "detail": f"Answer matches the query result ({shown})"}
            self.failures.append(("numbers", f"answer used {', '.join(bad + bad_names)} not in the result", 1))
        return code_answer(run), {"name": "Number", "passed": True,
                                  "detail": "Answer written from the result rows by code"}


def code_answer(run) -> str:
    from ..ingestion.chunk import format_value

    if len(run.rows) == 1 and len(run.columns) == 1:
        return f"{run.columns[0]}: {format_value(run.rows[0][0])}."
    if len(run.rows) == 1:
        return ", ".join(f"{c}: {format_value(v)}" for c, v in zip(run.columns, run.rows[0])) + "."
    more = " (first rows shown)" if run.truncated else ""
    return f"The query returned {len(run.rows)} rows{more}; see the result table."


def quote_in(table: str, sql: str) -> bool:
    return table in sql


def _ms(t: float) -> int:
    return int((time.perf_counter() - t) * 1000)
