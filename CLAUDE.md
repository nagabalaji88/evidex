# Evidex: local document Q&A agent

The full build spec is docs/spec.md. Read the relevant section before every task.
The milestone breakdown is docs/implementation-plan.md.
The UI must match design/reference-ui.html (open it, do not guess).

## Hard rules
- Backend: Python 3.12, FastAPI, in backend/. Front end: React + Vite + TypeScript, in frontend/.
- Everything runs locally. Ollama at http://localhost:11434. Never call cloud AI APIs.
- Every model name, threshold and limit comes from config.yaml. Never hard-code them.
- A milestone is done only when its acceptance criteria in the spec pass. Show me the evidence.
- Never edit docs/spec.md. If the spec looks wrong or unclear, stop and ask me.
- Never tune prompts or thresholds to fit specific test questions.
- Windows machine: use PowerShell-friendly commands and paths.

## Commands
- Backend venv: backend\.venv (create with python -m venv)
- Model servers and API keys: .env (copy .env.example; git-ignored). Local by default; remote hosts need EVIDEX_ALLOW_REMOTE=true
- Backend tests: cd backend; .venv\Scripts\python -m pytest -q
- Setup check (M1, writes docs/benchmarks.md): cd backend; .venv\Scripts\python -m app.setup_check
- Re-index (after changing the embedding model; stop the API first): cd backend; .venv\Scripts\python -m app.reindex
- Run API: cd backend; .venv\Scripts\python -m uvicorn app.main:app --reload
- Front end: cd frontend; npm install; npm run dev  (production: npm run build, then FastAPI serves it at http://localhost:8000)
- Front-end tests and types: cd frontend; npm test; npm run gen:types (API running) or npm run gen:types:file
- OpenAPI file for gen:types:file: cd backend; .venv\Scripts\python -m app.openapi_dump
- Evaluation: backend\.venv\Scripts\python eval\run_eval.py  [--compare] [--write-config]
- Regenerate eval data: backend\.venv\Scripts\python eval\make_sample_docs.py; ...\python eval\make_testset.py

## Progress (update after each milestone)
- [ ] M1 Setup check (code done; run app.setup_check on the i5 to write docs/benchmarks.md)
- [x] M2 Ingestion
- [ ] M3 Retrieval (code and tests done; the under-5 s timing is measured by the evaluation)
- [ ] M4 Answering (code and tests done; show one real answer and one refusal with Ollama)
- [x] M5 Grounding and table path
- [ ] M6 Evaluation (harness done; run eval\run_eval.py --compare on the i5, tune the threshold, commit the report)
- [x] M7 React UI
- [ ] M8 Good to have (spec section 11; already in: per-question log table, GET /api/traces/{id}, one question at a time)

## Compact instructions
When summarising, keep: the current milestone, failing tests, and decisions made.
