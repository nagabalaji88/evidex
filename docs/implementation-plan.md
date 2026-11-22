# Evidex: Implementation Plan

**Evidex** (evidence + index) is a local document Q&A agent. It answers only from your documents, shows the evidence and a confidence score for every answer, and refuses when the answer is not there.

- **Build spec:** `docs/spec.md` (single source of truth; this plan only orders the work)
- **UI reference:** `design/reference-ui.html`
- **Build tool:** Claude Code CLI, one milestone per conversation (see `docs/claude-code-guide.md`)
- **Machine:** Intel i5 13th gen desktop, 16 GB RAM, CPU only, Windows

---

## 1. Goal and definition of done

v1 is done when all of these are true:

1. All 7 pages work against the real backend, in dark and light mode, matching `design/reference-ui.html`.
2. The 50-case evaluation passes every target in spec section 7: 100% refusal on unanswerable questions, at least 90% correct answers and citations, 100% version and table accuracy.
3. Every acceptance criterion in spec sections 1 to 8 has a passing automated test or a recorded manual check.
4. A full answer takes under 30 s and search plus ranking under 5 s on the i5 desktop, measured by the evaluation.
5. Peak memory stays under 12 GB during upload, answering and evaluation.

---

## 2. Tech stack

| Layer | Choice |
| --- | --- |
| Answer model | `qwen2.5:7b-instruct` (Q4) through Ollama |
| Embeddings | `nomic-embed-text` through Ollama, with `search_query:` / `search_document:` prefixes |
| Reranker | `BAAI/bge-reranker-base` via `sentence-transformers`, CPU |
| Backend | Python 3.12, FastAPI, Uvicorn, Pydantic |
| Vector store | Chroma (local, persistent) |
| App database | SQLite: documents, versions, tables from spreadsheets, chat history, logs, settings |
| Keyword search | BM25 (`rank-bm25`) |
| File reading | `pypdf` / `pdfplumber`, `python-docx`, `openpyxl`, `pandas` |
| Front end | React, Vite, TypeScript, `openapi-typescript` for API types |
| Tests | `pytest` (backend), Vitest for front-end logic |
| Config | `config.yaml` (spec section 9) |

---

## 3. Repository layout

```text
evidex/
├── CLAUDE.md                  # rules for Claude Code (see the guide)
├── config.yaml                # every model, threshold and limit
├── docs/
│   ├── spec.md                # build spec
│   ├── implementation-plan.md # this file
│   └── claude-code-guide.md
├── design/
│   └── reference-ui.html      # UI reference, dark and light
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, serves frontend/dist in production
│   │   ├── config.py          # loads and validates config.yaml
│   │   ├── api/               # documents, ask, traces, stats, activity, eval, settings, models, health
│   │   ├── ingestion/         # validate, extract, chunk, embed, versioning, tables, dedup
│   │   ├── retrieval/         # route, rewrite, search (vector + BM25 + RRF), rerank, gate
│   │   ├── answer/            # prompt builder, generate, sentence numbering, citations
│   │   ├── grounding/         # quote, citation and number checks, normalisation
│   │   ├── sqlpath/           # table pick, SQL prompt, validate, run read-only
│   │   ├── store/             # sqlite.py, vectors.py
│   │   └── logs/              # per-question trace and log rows
│   ├── prompts/
│   │   ├── answer_prompt.md
│   │   └── sql_prompt.md
│   └── tests/
├── eval/
│   ├── sample_docs/           # incl. a v1/v2 pair, a CSV, an XLSX, a JSON
│   ├── testset.json           # 50 cases
│   ├── run_eval.py
│   └── report.md              # generated
├── frontend/
│   └── src/
│       ├── app/               # shell: icon menu, header, theme tokens
│       ├── pages/             # Dashboard, Upload, Chat, KnowledgeBase, Activity, Evaluation, Settings
│       ├── components/        # answer card, confidence box, evidence, steps panel, charts
│       └── api/               # client, stream reader, generated types
└── data/                      # chroma/, app.db (git-ignored)
```

---

## 4. Milestones

Estimates are focused working days with Claude Code doing the typing. Evenings-only work takes roughly twice as long on the calendar.

| # | Milestone | Spec sections | Estimate | Depends on |
| --- | --- | --- | --- | --- |
| M0 | Project setup | guide steps 1 to 4 | 0.5 day | none |
| M1 | Setup check and skeleton | 2, 9 | 0.5 day | M0 |
| M2 | Ingestion | 4 | 2 days | M1 |
| M3 | Retrieval | 5 | 1.5 days | M2 |
| M4 | Answering | 3, 8 (`/api/ask`) | 1 day | M3 |
| M5 | Grounding and table path | 5 (SQL), 6 | 1.5 days | M4 |
| M6 | Evaluation | 7 | 1.5 days | M5 |
| M7 | React UI, 7 pages | 8, 8.1 | 4 days | M4 (Chat, Upload), M6 (Evaluation page) |
| M8 | Good to have | 11 | 1.5 days | M7 |
| | **Total** | | **about 14 days** | |

### M0 · Project setup

- Install Git, Python 3.12, Node LTS, Ollama models and Claude Code.
- Create the repo with the layout above; add `docs/spec.md`, `design/reference-ui.html`, `CLAUDE.md`.
- First commit, tag `m0`.

**Done when:** `claude --version`, `ollama list` and `git log` all work, and CLAUDE.md is committed.

### M1 · Setup check and skeleton

- `backend/` venv, FastAPI hello endpoint, `config.py` loading `config.yaml`.
- `GET /api/health`: Ollama reachable, both models pulled, reranker loads.
- A benchmark script: RAM at idle and during one answer, tokens per second for Qwen 7B, embedding speed.
- Sample documents in `eval/sample_docs/` (real ones, or made-up ones as spec section 7 allows).

**Done when:** the health check is green and the benchmark numbers are written to `docs/benchmarks.md`. If Qwen 7B is slower than 3 tokens per second, stop and review the model choice before M2.

### M2 · Ingestion

- Validation (types, size, empty, scanned PDF), text extraction with pages and headings.
- Versioning with department, owner and size; the atomic switch; SHA-256 dedup.
- Structure-aware chunking with source locations per file type; embeddings into Chroma; BM25 index.
- Table path: CSV, XLSX and JSON records into SQLite with schema records.
- Re-index command. `POST /api/documents` streaming each step.

**Done when:** every section 4 acceptance criterion has a passing test. Key test: after uploading v2, no v1 chunk can be returned.

### M3 · Retrieval

- Rule-based routing (lookup vs calculation), follow-up rewriting with chat history.
- Active-version filter, hybrid search with RRF, reranker, threshold gate (reranker or vector threshold).
- Document scope filter: only documents chosen in the chat are searched.

**Done when:** section 5 criteria pass. Key tests: below the threshold the LLM is never called (checked in logs), and an exact clause number finds its chunk.

### M4 · Answering

- `prompts/answer_prompt.md`, numbered source sentences, Ollama JSON schema output.
- Per-sentence citations attached in code; error state for broken JSON (never a fake refusal).
- `POST /api/ask` streaming steps, then the answer; chat history in SQLite.

**Done when:** section 3 criteria pass, and one real answer and one refusal are shown from the sample documents.

### M5 · Grounding and the table path

- Citation, quote and number checks with normalisation; partial answers; retry once.
- Confidence score and level (High from 0.70, Medium from 0.45).
- SQL path: table pick, SQL prompt with schema and examples, validation, read-only run with timeout and row limit, number check.

**Done when:** section 5 (table path) and section 6 criteria pass. Key tests: a fabricated quote is never shown, `1,200` matches `1200`, a DELETE query is blocked.

### M6 · Evaluation

- `testset.json` with 50 cases in the spec mix: 18 answerable, 18 unanswerable, 5 table, 4 version traps, 3 prompt injection, 2 broken files.
- `run_eval.py` producing `report.md`; comparison runs (reranker on/off, final_k 3/5).
- Tune `relevance_threshold` and record it in `config.yaml`. `POST /api/eval/run` and `GET /api/eval/latest`.

**Done when:** every target in section 7 is met and the report is committed. No prompt or threshold tuned to individual test questions.

### M7 · React UI

Build in this order, one Claude Code conversation each:

1. **7a Shell:** Vite + React + TypeScript, Vite proxy, generated API types, icon menu (collapsed by default), header, dark and light theme tokens.
2. **7b Chat:** answer cards with confidence and evidence, steps panel, document picker, model picker, refusal and calculation cards.
3. **7c Upload:** drop zone, department and owner, live steps, recent uploads.
4. **7d Knowledge base:** filters, document table, versions with roll-back, sections or sample rows.
5. **7e Dashboard:** stat cards and charts from `GET /api/stats`.
6. **7f Activity log:** filters and the trace view.
7. **7g Evaluation:** run with progress, results, comparison, failures.
8. **7h Settings:** grouped controls, YAML preview, validation, re-run warning.

**Done when:** section 8 criteria pass in both modes, every page matches `design/reference-ui.html`, and `npm run build` is served by FastAPI at `http://localhost:8000`.

### M8 · Good to have

One commit each, from spec section 11: New chat button; per-question log table and `GET /api/traces/{id}`; Ollama timeouts and a one-at-a-time queue; answer cache cleared on version change; thumbs up/down.

**Done when:** each item works and appears where the spec says (Activity log, Dashboard).

---

## 5. How each milestone runs in Claude Code

1. `/clear`, then plan mode (`Shift+Tab` or `/plan`).
2. Paste the milestone prompt from the guide, or run `/milestone N`.
3. Review the plan against the spec; approve; switch to Accept edits.
4. Claude builds and writes a test for every acceptance criterion.
5. Run the tests yourself with `!`; ask the spec-checker subagent to confirm coverage.
6. `/review`, fix what matters.
7. Tick the milestone in CLAUDE.md, commit, and tag it (`m1`, `m2` …).

---

## 6. Testing strategy

| Level | What | When |
| --- | --- | --- |
| Unit | Chunking, normalisation, SQL validation, versioning, confidence levels | Every milestone, `pytest -q` |
| Integration | Upload → search → answer against real Ollama on sample documents | M4 onwards |
| Evaluation | The 50-case set with targets | M6, then after any change to the model, prompt, thresholds or chunking |
| UI | Click-through of every page in both modes against the reference file | M7, optionally with the Playwright MCP |
| Performance | Answer time, search time, peak RAM | M1 baseline, then from every evaluation run |

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Qwen 7B too slow on CPU | Answers over 30 s | Measure in M1; keep `num_ctx` 4096 and 3 passages; stream steps so waits feel shorter |
| RAM above 12 GB | Swapping, freezes | `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_NUM_PARALLEL=1`, reranker threads limited; measured every evaluation |
| Weak refusals on "related but not answered" questions | Hallucinated answers | Reranker on by default; threshold tuned on 18 trick questions; grounding checks |
| 7B model writes wrong SQL | Wrong numbers | Schema plus examples in the prompt; validation; the SQL is always shown |
| Claude Code drifts from the spec | Rework | Plan mode first; CLAUDE.md rules; spec-checker subagent; one milestone per session |
| Claude plan usage limits | Slower progress | One milestone per session, `/compact`, local runs for slow steps |

---

## 8. Git workflow

- `main` always works. One branch per milestone (`m2-ingestion` …), merged when its checks pass.
- Commit after every working step; tag each finished milestone.
- `data/`, `.venv/`, `node_modules/` and `CLAUDE.local.md` are in `.gitignore`.
- From M7, backend and front-end work can run in parallel with `claude --worktree`.

---

## 9. After v1

Bigger evaluation set, logins and per-user document access, a second evaluated answer model, after-the-answer citation matching, embedding cache, tracing with a tool such as Langfuse.
