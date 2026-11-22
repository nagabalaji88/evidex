# Evidex

Evidex is a local document Q&A agent. It answers questions only from the documents you upload,
shows the evidence and a confidence score for every answer, and refuses when the answer is not there.
Everything runs on your machine with Ollama: no cloud AI, no API keys.

## What is in this repo

| Path | What it is |
| --- | --- |
| `docs/spec.md` | The build spec: the single source of truth |
| `docs/implementation-plan.md` | Milestones M0 to M8, with tasks and checks |
| `docs/claude-code-guide.md` | How to build it step by step with Claude Code |
| `design/reference-ui.html` | The UI to match (open it in a browser; dummy data) |
| `config.yaml` | Every model name, threshold and limit |
| `CLAUDE.md` | Rules Claude Code reads at the start of every session |
| `.claude/skills/milestone/` | The `/milestone N` command |
| `eval/sample_docs/` | Documents for the evaluation set (created in M1) |

## Requirements

- Windows 10/11, 16 GB RAM, CPU only (built for an Intel i5 13th gen desktop)
- Python 3.12, Node.js LTS, Git
- Ollama with the two models:

```powershell
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text
```

Start Ollama with these set, so only two models stay loaded and one request runs at a time:

```powershell
$env:OLLAMA_MAX_LOADED_MODELS = "2"
$env:OLLAMA_NUM_PARALLEL = "1"
ollama serve
```

## Getting started

```powershell
git init
git add .
git commit -m "Initial commit: spec, plan, UI reference and project rules"
claude
```

Then follow `docs/claude-code-guide.md`, starting with `/milestone 1`.

## Running

```powershell
# Backend (first time)
cd backend; python -m venv .venv
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pytest -q                         # backend tests
.venv\Scripts\python -m app.setup_check                   # health + benchmark -> docs/benchmarks.md

# Front end (first time, and after UI changes)
cd frontend; npm install; npm test; npm run build

# Daily use: one process, one URL -> http://localhost:8000
cd backend; .venv\Scripts\python -m uvicorn app.main:app

# Evaluation -> eval/report.md (add --compare for reranker on/off x final_k 3/5,
# and --write-config to record the tuned threshold and cheapest passing setting)
backend\.venv\Scripts\python eval\run_eval.py --compare
```

While working on the UI, run the API with `--reload` and `cd frontend; npm run dev` (Vite proxies `/api`
to port 8000). After changing an API model, run `npm run gen:types` with the API running.
