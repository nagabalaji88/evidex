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

By default answers and embeddings come from the Google Gemini API (`provider.use_local_models:
false` in `config.yaml`). Open Settings > AI models, paste an API key from
aistudio.google.com/api-keys and click Save key: Evidex checks it with Google, saves it in `.env`
as `EVIDEX_GEMINI_API_KEY` (never in `config.yaml`, never shown again), and lists the models that
key can use. Only models in `gemini.free_tier_models` (taken from Google's pricing page) are listed
or accepted while `gemini.free_tier_only` is true. Pick the answer and embedding models, then Save.

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

On Windows, `evidex.bat` in the repo root does the steps below for you. It installs only what is
missing (backend\.venv and packages, .env, the front-end build), then starts the app. It does not
pull Ollama models: set the model server, model names and API key in `.env`.

```powershell
.\evidex.bat           # install if needed, then run at http://localhost:8000
.\evidex.bat dev       # API with --reload plus the Vite dev server on http://localhost:5173
.\evidex.bat install   # install or update everything, then stop
```

The same steps by hand:

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
