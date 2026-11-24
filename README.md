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

## Gemini API or local models (Settings > AI models)

By default answers and embeddings come from an API connection (`provider.use_local_models: false`),
set up in Settings > AI models in two steps:

1. **Connect:** enter the API key and the models URL (Google Gemini:
   `https://generativelanguage.googleapis.com/v1beta/models`; any OpenAI-style service: its
   `.../v1/models`), then click **OK**. Evidex lists every model that key can use.
2. **Choose a model for each task:** answer and embedding models, and optionally separate models
   for follow-up rewriting, question routing, SQL writing and the claim check. Click **OK**: the
   key and every model are checked with the API, the key is saved in `.env` as
   `EVIDEX_GEMINI_API_KEY` (never in `config.yaml`, never shown again), and the URL and models in
   `config.yaml`. Then upload your documents and ask.

For Google's API, each model is marked free tier or paid only; while `gemini.free_tier_only` is true,
only models in `gemini.free_tier_models` (from Google's pricing page) can be picked. Settings is the
only place models are chosen: the Chat page shows the answer model in use and links to Settings.

- The free tier applies only when the key's Google Cloud project has no billing account. With
  billing on, Google charges for every request, whichever model you pick.
- On the free tier Google may use what is sent (questions and document passages) to improve its
  products. The rest of Evidex (files, index, logs) stays on your computer.
- Free-tier rate limits answer HTTP 429: Evidex waits and retries (`gemini.rate_limit_retries`).
- Turn on "Use local models" to list and use local models (Ollama or the `.env` server) instead.
  Switching changes the embedding model, so re-index (the page offers it after saving).

## Models, servers and API keys (.env)

Copy `.env.example` to `.env` (git-ignored) to change where models run, without touching code:
local Ollama (default), or any OpenAI-compatible server such as LM Studio, llama.cpp, vLLM or
LocalAI, with an optional API key. Chat and embeddings can use different servers. Values in
`.env` override `config.yaml`; the Settings page marks them "Set in .env". Public internet
hosts are refused unless you set `EVIDEX_ALLOW_REMOTE=true`, because that sends your documents'
text to that service. Restart the API after editing `.env`; changing the embedding model
needs a re-index.

```powershell
Copy-Item .env.example .env
```

## Running

On Windows, double-click `evidex.bat` in the repo root (or run `.\evidex.bat`). Every run:

1. Backend: creates `backend\.venv` (Python 3.12) and installs `backend\requirements.txt`, again
   whenever that file changes.
2. Settings: creates `.env` from `.env.example` if it is missing.
3. Front end: installs the npm packages (again when `package-lock.json` changes) and builds the
   web page, again whenever the front-end code changes (for example after a `git pull`).
4. Launch: starts Evidex at http://localhost:8000 and opens it in your browser.

It needs Python 3.12 and Node.js LTS. It installs and starts no local AI models: answers come from
the Gemini API, set up in Settings. The optional local reranker (PyTorch and a 1 GB model) is in
`backend\requirements-local.txt` and is off by default (`reranker.enabled: false`).

The same steps by hand:

```powershell
# Backend (first time)
cd backend; python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
# Optional local reranker only: requirements-local.txt (see the comments in that file)
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
