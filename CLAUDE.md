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
- Backend tests: cd backend; .venv\Scripts\python -m pytest -q
- Setup check (M1, writes docs/benchmarks.md): cd backend; .venv\Scripts\python -m app.setup_check
- Re-index (after changing the embedding model; stop the API first): cd backend; .venv\Scripts\python -m app.reindex
- Run API: cd backend; .venv\Scripts\python -m uvicorn app.main:app --reload
- Front end: cd frontend; npm run dev  (production: npm run build)
- Evaluation: backend\.venv\Scripts\python eval\run_eval.py

## Progress (update after each milestone)
- [ ] M1 Setup check
- [ ] M2 Ingestion
- [ ] M3 Retrieval
- [ ] M4 Answering
- [ ] M5 Grounding and table path
- [ ] M6 Evaluation
- [ ] M7 React UI
- [ ] M8 Good to have (spec section 11)

## Compact instructions
When summarising, keep: the current milestone, failing tests, and decisions made.
