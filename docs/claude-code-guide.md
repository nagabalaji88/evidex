# Building Evidex with Claude Code: Step-by-Step Guide

This guide takes you from an empty folder to a working Evidex agent, using only Claude Code in the terminal, and teaches the main CLI features along the way.

## What Claude Code is, and what you need

Claude Code is Claude working inside your terminal. You type a request in plain English; it reads your project files, writes and edits code, runs commands such as tests, and shows you every change. It asks before risky actions, and you can undo its file edits.

Think of it as a fast junior developer sitting at your keyboard: you give the direction and check the work, it does the typing. Your build spec is its job description.

**Two separate things, don't mix them up:**

- **Claude Code** writes the code. It runs on Anthropic's cloud models, so it needs a Claude account.
- **Your app** answers questions with Qwen through Ollama, fully on your machine. Claude Code never runs inside your app.

**What you need before you start:**

| Item | Why | Notes |
| --- | --- | --- |
| A Claude Pro or Max subscription | To sign in to Claude Code | No API key needed. A long build can hit Pro usage limits; Max gives more room |
| Windows 10 or 11 with PowerShell | Where you will run everything | Use the Windows Terminal app for a better experience |
| Git for Windows | Version history, and Claude Code uses its Bash shell | Recommended by Anthropic for native Windows |
| Python 3.12 | The FastAPI backend | Tick "Add to PATH" in the installer |
| Node.js LTS | The React front end | Not needed for Claude Code itself |
| Ollama with `qwen2.5:7b-instruct` and `nomic-embed-text` | The models your app uses | Already partly done on your machine |
| This repository | The spec, the UI reference, CLAUDE.md and config.yaml | Already in the initial-commit zip |

This guide assumes Windows on your i5 desktop. On macOS or Linux, only the install commands change.

## Step 1: Install the tools

1. Install **Git for Windows**, **Python 3.12** and **Node.js LTS** from their official sites, with default options.
2. Open **Windows Terminal** (PowerShell). Your prompt starts with `PS C:\`.
3. Install Claude Code:

```powershell
irm https://claude.ai/install.ps1 | iex
```

4. Close the terminal, open a new one, and check every tool:

```powershell
claude --version
git --version
python --version
node --version
ollama list
```

Each should print a version. `ollama list` should show both models; if not, run `ollama pull qwen2.5:7b-instruct` and `ollama pull nomic-embed-text`.

If `claude` is "not recognized", the install folder is not on your PATH yet: restart the terminal first, then see Anthropic's "Troubleshoot installation" page. The native install updates itself in the background.

## Step 2: Sign in

```powershell
claude
```

The first time, it opens your browser to log in. Choose your Claude account (Pro or Max). After that you stay signed in. Type `/exit` to leave for now.

## Step 3: Set up the project folder

Unzip `evidex-initial-commit.zip` into `C:\projects\`, so you get `C:\projects\evidex\`. It already contains:

- `docs\spec.md` (the build spec), `docs\implementation-plan.md` and this guide
- `design\reference-ui.html` (the UI to match; open it in a browser)
- `CLAUDE.md`, `config.yaml`, `.gitignore`, `README.md`
- `.claude\skills\milestone\SKILL.md` (the `/milestone` command from step 6)

Optional: put a few real PDFs, Word files or spreadsheets in `eval\sample_docs\`. If you skip this, Claude Code creates made-up ones in milestone 1, as the spec allows.

Turn it into a Git repository and save the starting point:

```powershell
cd C:\projects\evidex
git init
git add .
git commit -m "Initial commit: spec, plan, UI reference and project rules"
```

Committing first matters: it gives you a clean point to go back to, and some Claude Code features (worktrees) need at least one commit.

## Step 4: First session and the project memory (CLAUDE.md)

Every Claude Code session starts with a blank memory. `CLAUDE.md` is a file in your project root that it reads at the start of **every** session. Put the rules you never want to repeat there. Keep it under about 200 lines; longer files are followed less reliably.

1. Start Claude Code in the project and say hello:

```powershell
cd C:\projects\evidex
claude
```

2. Ask something to see it read files: `read @docs/spec.md and summarise the 11 sections in 11 lines`. The `@` pulls a file straight into the conversation; type `@` and a menu of paths appears.
3. `CLAUDE.md` is already in the repo, so you don't need `/init`. Open it with `/memory` and adjust anything you like. Its contents:

```markdown
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
```

4. If you changed it, type `commit CLAUDE.md with a clear message`. Claude runs the Git commands for you.

**Good to know:**

- `/memory` opens your memory files for editing.
- A `CLAUDE.local.md` next to it holds private notes that should not go into Git (it is already in `.gitignore`).
- Claude Code also keeps its own **auto memory** of things you correct it on. You can see and switch it off in `/memory`.

## The CLI controls to learn (cheat sheet)

Type `/` at any time to see every command, and `/powerup` for short interactive lessons. Learn the first table now; the rest as you meet them in the milestones.

**Permission modes: how much Claude may do without asking.** Press `Shift+Tab` to cycle them; the mode shows below the prompt. On Windows terminals where `Shift+Tab` does nothing, use `Alt+M`.

| Mode | What it does | When to use it here |
| --- | --- | --- |
| Manual (default) | Asks before every file edit and command | First sessions, while you learn |
| Accept edits | Edits files without asking; still asks before other commands | After you approve a plan |
| Plan | Reads and proposes a plan; changes nothing | The start of every milestone |
| Auto | A safety check approves safe actions and blocks risky ones | Later, once you trust the setup |

**Daily commands and keys:**

| Command or key | What it does |
| --- | --- |
| `claude` | Start a session in the current folder |
| `claude -c` | Continue your last session in this folder |
| `claude -r` or `/resume` | Pick an older session to resume |
| `/plan <task>` | Jump into plan mode with a task |
| `@path` | Put a file or folder into the conversation |
| `!command` | Run a shell command yourself, for example `!git status`; Claude sees the output |
| `Esc` | Stop Claude right now, then redirect it |
| `Esc` `Esc` or `/rewind` | Undo: go back to an earlier point, code and/or conversation |
| `/clear` | Fresh conversation; use it between milestones |
| `/compact <focus>` | Summarise the conversation to free space, keeping what you name |
| `/context` | Show what is filling Claude's memory window |
| `/usage` (or `/cost`) | Show usage and your plan limits |
| `/model` and `/effort` | Switch model, and how hard it thinks |
| `/memory` | Edit CLAUDE.md and auto memory |
| `/permissions` | Allow commands you trust, such as `pytest`, so it stops asking |
| `/review` | Review your current changes for bugs |
| `/btw <question>` | Ask a side question without cluttering the conversation |
| `/doctor` | Check and fix your Claude Code setup |
| `/export` | Save the conversation to a file |
| `Ctrl+O` | Show every tool call in detail |
| `Ctrl+T` | Show Claude's task checklist |
| `Ctrl+B` | Move a long-running command, such as the API server, to the background |
| `Ctrl+R` | Search your past prompts |
| `Ctrl+G` | Write a long prompt in your text editor |
| `Alt+V` | Paste a screenshot (Windows) |
| `Shift+Enter` | New line in your prompt without sending |
| `/exit` or `Ctrl+D` twice | Leave Claude Code |

You can also simply ask it: "how do I allow pytest without being asked every time?" Claude Code answers from its own docs.

## Step 5: Build one milestone at a time

Never paste the whole spec and say "build it". Build the milestones of spec section 10 one by one, each in a fresh conversation, with the same eight-step loop:

1. **Fresh start:** `/clear` (or a new `claude` session).
2. **Plan mode:** press `Shift+Tab` until it shows plan mode, or use `/plan`.
3. **Paste the milestone prompt** from the list below (or run `/milestone N`).
4. **Read the plan.** Ask questions, push back, and ask for changes until it matches the spec. This is where you think like the architect.
5. **Approve** and switch to Accept edits. Watch it work; press `Esc` to redirect at any time.
6. **Check it yourself:** run the tests or the app with `!`, for example `!cd backend; .venv\Scripts\python -m pytest -q`. Ask for the acceptance evidence if it did not show it.
7. **Review:** run `/review` and fix what matters.
8. **Save:** "tick M1 in CLAUDE.md and commit with a clear message".

### The milestone prompts

Each prompt points at the spec rather than repeating it, so the spec stays the single source of truth. Each also names the Claude Code feature to try in that milestone.

**M1 · Setup check** (try: `!` commands and `@` references)

```text
Milestone 1 from @docs/spec.md section 10. Create the project skeleton: backend/ with a
Python venv and FastAPI, check config.yaml against section 9, and fill eval/sample_docs/
as section 7 describes. Then write a script that checks Ollama is reachable, both models
are pulled, the reranker loads, and measures RAM and tokens per second for one Qwen answer.
Run it and show me the numbers. Do not start milestone 2.
```

**M2 · Ingestion** (try: plan mode, then Accept edits)

```text
Milestone 2: implement section 4 (ingestion) of @docs/spec.md, including department and
owner, versioning with the atomic switch, dedup, re-index, and the table path for
CSV, Excel and JSON. Write pytest tests for every acceptance criterion in section 4
and run them. Show me which criterion each test covers.
```

**M3 · Retrieval** (try: a subagent for research)

```text
Milestone 3: implement section 5 (retrieval) of @docs/spec.md: active-version filter,
hybrid search with RRF, the reranker, the threshold gate, rule-based routing and
follow-up rewriting. Use a subagent to check how nomic-embed-text expects its
search_query and search_document prefixes before you code. Tests for every
section 5 criterion.
```

**M4 · Answering** (try: `/compact` when the conversation gets long)

```text
Milestone 4: implement section 3 of @docs/spec.md: the prompt file, numbered source
sentences, Ollama format with the JSON schema, per-sentence citations, the error state
for bad JSON, and the POST /api/ask endpoint streaming the steps from section 8.
Test with the sample documents and show me one real answer and one refusal.
```

**M5 · Grounding and the table path** (try: `/review`)

```text
Milestone 5: implement section 6 (grounding checks, with number and date normalisation)
and the SQL path of section 5 with every safety rule. Tests for each acceptance
criterion in sections 5 and 6, including a fabricated quote, a wrong number and a
write query that must be blocked.
```

**M6 · Evaluation** (try: the `/milestone` command from step 6)

```text
Milestone 6: build the section 7 evaluation: the 50-case test set, eval/run_eval.py,
the reranker on/off and final_k 3/5 comparison runs, and eval/report.md. Run it on
my machine, tune relevance_threshold as section 7 says, and record it in config.yaml.
Show me the report. Do not tune prompts to specific test questions.
```

**M7 · React UI** (try: pasting a screenshot with `Alt+V`, and `Ctrl+B` for the dev servers)

Split this into sub-steps, one conversation each, in this order: app shell (menu, header, dark and light themes) → Chat → Upload → Knowledge base → Dashboard → Activity log → Evaluation → Settings. First prompt:

```text
Milestone 7a: set up frontend/ (Vite, React, TypeScript) as section 8.1 of
@docs/spec.md says, with the Vite proxy and generated API types. Build only the app
shell: the collapsible icon menu, the header, and dark and light themes with CSS
variables. Match @design/reference-ui.html exactly. Start the API and the dev
server in the background and tell me the URL to check.
```

For each next page: "Milestone 7b: build the Chat page from section 8 of the spec, matching the reference file. Wire it to the real API." Change the page name each time.

**M8 · Good to have** (try: running two sessions in parallel with worktrees)

```text
Milestone 8: implement section 11 of @docs/spec.md, one row at a time: New chat,
the log table and GET /api/traces, Ollama timeouts and the queue, the answer cache,
thumbs up/down. Commit after each row.
```

## Step 6: Going further (the real power of the CLI)

Try these once milestones 1 to 3 feel comfortable. Each one turns something you keep typing into something automatic. You can ask Claude Code to create any of them for you.

| Feature | What it is | Use it in this project for |
| --- | --- | --- |
| Custom command (skill) | A saved prompt you run with `/name` | `/milestone 4`: run the whole build loop for a milestone |
| Subagent | A helper Claude with its own memory window, which reports back a summary | A spec checker that compares code against acceptance criteria |
| Hook | A command that always runs at a set moment, such as after every edit | Auto-formatting Python after each change |
| MCP server | A plug-in that gives Claude new tools | A browser tool to click through your UI and compare it with the reference |
| Headless mode | Run Claude once from a script, no chat | A nightly evaluation summary |
| Worktrees | Two sessions on separate copies of the code at once | Backend and front end in parallel in M7 and M8 |

### The `/milestone` command

It is already in the repo at `.claude\skills\milestone\SKILL.md`:

```markdown
---
name: milestone
description: Plan and build one milestone of the Evidex spec
disable-model-invocation: true
---

Work on milestone $ARGUMENTS from docs/spec.md section 10.

1. Read the spec sections this milestone covers, its tasks in docs/implementation-plan.md, and CLAUDE.md.
2. Propose a plan and wait for my approval.
3. Build it, with a pytest test for every acceptance criterion.
4. Run the tests and show which criterion each one covers.
5. Tick the milestone in CLAUDE.md and propose a commit message.
```

`/milestone 4` runs the whole loop. `$ARGUMENTS` is whatever you type after the name.

### A spec-checker subagent

Ask: "create a project subagent called spec-checker that reads docs/spec.md and the code, and reports every acceptance criterion as met, not met or untested. Read-only tools." It is saved under `.claude\agents\`. Then, at the end of each milestone: "use the spec-checker subagent on milestone 4". It does its reading in its own window, so your main conversation stays small.

### A formatting hook

Ask: "add a project hook that runs ruff format on any Python file you edit". It goes into `.claude\settings.json` with this shape:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [{ "type": "command", "command": "<the format command>" }]
      }
    ]
  }
}
```

Hooks run every time, whatever the model decides. That makes them the right place for rules that must never be skipped. `/hooks` shows what is configured.

### A browser tool through MCP

Ask: "add the Playwright MCP server to this project", then check it with `/mcp`. In M7 you can then say "open localhost:8000, click through every page in dark and light mode, and compare each with design/reference-ui.html".

### Headless mode

Run one prompt from PowerShell without opening a session:

```powershell
claude -p "run the evaluation and summarise any failures in 5 lines"
```

This is how Claude Code fits into scripts and scheduled jobs.

### Parallel work with worktrees

In two terminals:

```powershell
claude --worktree backend-m8
claude --worktree frontend-pages
```

Each session gets its own copy of the code on its own branch, so the two never overwrite each other. Merge them with Git when both are done.

## Tips, common mistakes and fixes

**Habits that pay off:**

- **One milestone per conversation.** `/clear` between them. A long conversation gets summarised automatically and early instructions can fade; CLAUDE.md never does.
- **Plan before code, every time.** Fixing a plan costs a minute; fixing 30 edited files costs an hour.
- **Be specific.** "The grounding check rejects 1,200 vs 1200; normalise as section 6 says" beats "fix grounding".
- **Commit after every milestone.** Git is your long-term undo; `Esc Esc` is your short-term one.
- **Make it prove things.** Always ask for the test output or the evaluation report, not just "done".
- **Watch `/usage`.** Embedding and evaluation runs are slow on your CPU, but they run locally and cost nothing. The planning and coding is what uses your Claude plan.

**When things go wrong:**

| Problem | What to do |
| --- | --- |
| Claude goes off in the wrong direction | `Esc`, then tell it what is wrong. Or type a correction while it works; it reads it before its next step |
| It broke something that worked | `Esc Esc` (or `/rewind`) to an earlier point, or `!git diff` and ask it to undo one file |
| It keeps forgetting a rule | Put the rule in CLAUDE.md, then `/clear` |
| It wants to change the spec or a threshold to pass a test | Say no. The spec and the evaluation are the judge, not the code |
| Tests are slow because Ollama is cold | Start Ollama first; `Ctrl+B` sends the API server to the background |
| "Context is full" warnings | `/compact focus on milestone N and failing tests`, or finish up and `/clear` |
| It keeps asking to run `pytest` | `/permissions` → allow that command |
| The terminal looks garbled | `Ctrl+L` redraws the screen |
| Anything odd with the install or settings | `/doctor` |

**What to learn next:** Anthropic's free "Claude Code 101" course on Claude Academy, and the Claude Code docs pages listed below.

### Sources

- [Claude Code quickstart](https://code.claude.com/docs/en/quickstart)
- [Common workflows](https://code.claude.com/docs/en/common-workflows)
- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)
- [Memory and CLAUDE.md](https://code.claude.com/docs/en/memory)
- [Commands reference](https://code.claude.com/docs/en/commands)
- [Interactive mode and shortcuts](https://code.claude.com/docs/en/interactive-mode)
- [Skills](https://code.claude.com/docs/en/skills)
- [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [Subagents](https://code.claude.com/docs/en/sub-agents)
- [MCP](https://code.claude.com/docs/en/mcp)
- [Headless mode](https://code.claude.com/docs/en/headless)
