"""Milestone 1 setup check and benchmark.

Checks that Ollama is reachable, both models are pulled, the reranker loads and the
vector store opens; then measures RAM at idle and during one answer, Qwen tokens per
second, embedding speed and rerank time. Writes the numbers to docs/benchmarks.md.

Run on the target machine, with Ollama started:
    cd backend; .venv\\Scripts\\python -m app.setup_check
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psutil

from .answer.schema import ANSWER_SCHEMA
from .config import REPO_ROOT, Config, load_config
from .health import RerankerState, check_models, check_vector_store
from .llm import close_clients, make_clients
from .ollama_client import OllamaError
from .reranker import load_reranker
from .store.vectors import open_vector_store

# Review gates from docs/implementation-plan.md (M1) and spec section 2. These are
# acceptance limits for the benchmark, not application settings, so they are not in config.
MIN_TOKENS_PER_SECOND = 3.0
MAX_PEAK_RAM_GB = 12.0

GB = 1024**3
SAMPLE_DOC = REPO_ROOT / "eval" / "sample_docs" / "store_operations_manual.md"
REPORT_PATH = REPO_ROOT / "docs" / "benchmarks.md"


# -- RAM sampling -----------------------------------------------------------------------


def system_used_bytes() -> int:
    vm = psutil.virtual_memory()
    return vm.total - vm.available


def process_rss(name_part: str) -> int:
    total = 0
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            if name_part in (p.info["name"] or "").lower() and p.info["memory_info"]:
                total += p.info["memory_info"].rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


class PeakSampler:
    """Samples system-wide used RAM in a background thread and keeps the peak."""

    def __init__(self, interval: float = 0.1):
        self.interval = interval
        self.peak = system_used_bytes()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, system_used_bytes())
            self._stop.wait(self.interval)

    def __enter__(self) -> "PeakSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, system_used_bytes())


# -- benchmark --------------------------------------------------------------------------


@dataclass
class Report:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)
    numbers: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sample_answer: str = ""

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.checks) and not self.warnings


def _passages(config: Config, n: int) -> list[str]:
    """n passages of roughly chunk_size_tokens each (about 4 characters per token)."""
    text = SAMPLE_DOC.read_text(encoding="utf-8") if SAMPLE_DOC.is_file() else ""
    if len(text) < 500:
        text = (
            "Store staff must log every refund in the point-of-sale system before the end of the shift. "
            "Refunds above the store manager's approval limit are escalated to the regional office. "
        ) * 40
    size = config.ingestion.chunk_size_tokens * 4
    while len(text) < size * n:
        text += "\n" + text
    return [text[i * size : (i + 1) * size] for i in range(n)]


def _answer_messages(config: Config) -> list[dict[str, str]]:
    """A prompt the size of a real answer call: final_k passages split into numbered sentences."""
    sentences: list[str] = []
    for p in _passages(config, config.retrieval.final_k):
        sentences.extend(s.strip() for s in p.replace("\n", " ").split(". ") if s.strip())
    numbered = "\n".join(f"[S{i}] {s}" for i, s in enumerate(sentences, 1))
    prompt = (
        "Answer ONLY using the numbered source sentences below. Cite sentence IDs. "
        "Respond in JSON.\n\nSources:\n" + numbered + "\n\nQuestion: Who approves refunds above the store limit?"
    )
    return [{"role": "user", "content": prompt}]


class _Clients:
    """(chat client, embedding client) from config + .env, closed on exit."""

    def __init__(self, config: Config):
        self.clients = make_clients(config)

    async def __aenter__(self):
        return self.clients

    async def __aexit__(self, *exc):
        await close_clients(*self.clients)


async def run(config: Config, *, write: bool = True) -> Report:
    report = Report()
    idle_used = system_used_bytes()
    total = psutil.virtual_memory().total

    async with _Clients(config) as (ollama, embedder):
        reach, answer_ok, embed_ok = await check_models(config, ollama, embedder)
        report.checks += [
            (f"{ollama.label} reachable", reach.ok, reach.detail),
            (f"Answer model `{config.models.answer}` pulled", answer_ok.ok, answer_ok.detail),
            (f"Embedding model `{config.models.embedding}` pulled", embed_ok.ok, embed_ok.detail),
        ]
        store = check_vector_store(lambda: open_vector_store(config))
        report.checks.append(("Vector store opens", store.ok, store.detail))

        rss_before = psutil.Process().memory_info().rss
        rstate = RerankerState.load(config, load_reranker)
        rcheck = rstate.check()
        report.checks.append((f"Reranker `{config.reranker.model}` loads", rcheck.ok, rcheck.detail))
        if rstate.reranker is not None:
            report.numbers.append(("Reranker load time", f"{rstate.load_seconds:.1f} s"))
            report.numbers.append(
                ("Reranker RAM (process RSS growth)", f"{(psutil.Process().memory_info().rss - rss_before) / GB:.2f} GB")
            )

        report.numbers += [
            ("RAM total", f"{total / GB:.1f} GB"),
            ("RAM in use at idle (system-wide, before models load)", f"{idle_used / GB:.2f} GB"),
        ]

        if not (reach.ok and answer_ok.ok and embed_ok.ok):
            report.warnings.append("The model server or a model is missing; speed and RAM during an answer were not "
                                   "measured.")
            return _finish(report, config, write)

        with PeakSampler() as full_question:
            # Embedding speed: one batch of document chunks, then one query.
            chunks = _passages(config, config.ingestion.embed_batch_size)
            t = time.perf_counter()
            await embedder.embed_documents(chunks)  # first call also loads the model
            cold_embed = time.perf_counter() - t
            t = time.perf_counter()
            await embedder.embed_documents(chunks)
            warm_embed = time.perf_counter() - t
            t = time.perf_counter()
            await embedder.embed_query("Who approves refunds above the store limit?")
            query_ms = (time.perf_counter() - t) * 1000
            report.numbers += [
                (f"Embedding: {len(chunks)} chunks of ~{config.ingestion.chunk_size_tokens} tokens, cold", f"{cold_embed:.2f} s"),
                (f"Embedding: {len(chunks)} chunks, warm", f"{warm_embed:.2f} s ({len(chunks) / warm_embed:.1f} chunks/s)"),
                ("Embedding: one query", f"{query_ms:.0f} ms"),
            ]

            # Rerank candidate_k passages in one batch.
            if rstate.reranker is not None:
                cands = _passages(config, config.retrieval.candidate_k)
                t = time.perf_counter()
                scores = rstate.reranker.score("Who approves refunds above the store limit?", cands)
                report.numbers.append(
                    (
                        f"Rerank: {len(cands)} pairs in one batch",
                        f"{time.perf_counter() - t:.2f} s (scores {min(scores):.2f}..{max(scores):.2f})",
                    )
                )

            # One answer with the JSON schema, cold (includes model load) then warm.
            messages = _answer_messages(config)
            try:
                t = time.perf_counter()
                cold = await ollama.chat(messages, schema=ANSWER_SCHEMA)
                cold_s = time.perf_counter() - t
                warm = await ollama.chat(messages, schema=ANSWER_SCHEMA)
            except OllamaError as exc:
                report.checks.append(("Answer call", False, str(exc)))
                return _finish(report, config, write)

        try:
            json.loads(warm.content)
            parsed = True
        except json.JSONDecodeError:
            parsed = False
        report.checks.append(("Answer output parses as JSON (format = schema)", parsed, f"{len(warm.content)} chars"))
        report.sample_answer = warm.content

        tps = warm.tokens_per_second
        report.numbers += [
            ("Answer: first call incl. model load", f"{cold_s:.1f} s"),
            ("Answer: prompt tokens / output tokens", f"{warm.prompt_tokens} / {warm.output_tokens}"),
            ("Answer: prompt processing", f"{warm.prompt_tokens_per_second:.1f} tokens/s"),
            ("Answer: generation speed", f"{tps:.2f} tokens/s"),
            ("Answer: warm call total", f"{warm.total_ms / 1000:.1f} s"),
            ("Ollama processes RSS after answer", f"{process_rss('ollama') / GB:.2f} GB"),
            ("Peak RAM in use during embed + rerank + answer (system-wide)", f"{full_question.peak / GB:.2f} GB"),
        ]
        if tps < MIN_TOKENS_PER_SECOND:
            report.warnings.append(
                f"Generation is {tps:.2f} tokens/s, below {MIN_TOKENS_PER_SECOND:g}: stop and review the model "
                "choice before M2 (implementation plan, M1)."
            )
        if full_question.peak / GB >= MAX_PEAK_RAM_GB:
            report.warnings.append(
                f"Peak RAM {full_question.peak / GB:.1f} GB is not under {MAX_PEAK_RAM_GB:g} GB (spec section 2)."
            )
        if warm.prompt_tokens >= config.ollama.num_ctx:
            report.warnings.append(
                f"Prompt used {warm.prompt_tokens} tokens of num_ctx {config.ollama.num_ctx}: context may be truncated."
            )

    return _finish(report, config, write)


def _finish(report: Report, config: Config, write: bool) -> Report:
    if write:
        REPORT_PATH.write_text(render(report, config), encoding="utf-8")
    return report


def render(report: Report, config: Config) -> str:
    cpu = platform.processor() or platform.machine()
    lines = [
        "# Benchmarks (milestone 1 setup check)",
        "",
        f"Generated by `python -m app.setup_check` on {datetime.now():%Y-%m-%d %H:%M}.",
        "",
        f"- Machine: {platform.system()} {platform.release()}, {cpu}, "
        f"{psutil.cpu_count(logical=False)} cores / {psutil.cpu_count()} threads, "
        f"{psutil.virtual_memory().total / GB:.1f} GB RAM",
        f"- Python {platform.python_version()}",
        f"- Models: answer `{config.models.answer}`, embedding `{config.models.embedding}`, "
        f"reranker `{config.reranker.model}` ({'enabled' if config.reranker.enabled else 'disabled'})",
        f"- Settings: num_ctx {config.ollama.num_ctx}, num_predict {config.ollama.num_predict}, "
        f"temperature {config.ollama.temperature}, final_k {config.retrieval.final_k}, "
        f"candidate_k {config.retrieval.candidate_k}, reranker threads {config.reranker.cpu_threads}",
        "",
        "## Checks",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {name} | {'PASS' if ok else 'FAIL'} | {detail} |" for name, ok, detail in report.checks]
    lines += ["", "## Numbers", "", "| Measure | Value |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in report.numbers]
    lines += ["", "## Verdict", ""]
    if report.ok:
        lines.append(
            f"All checks pass. Generation is at least {MIN_TOKENS_PER_SECOND:g} tokens/s and peak RAM is under "
            f"{MAX_PEAK_RAM_GB:g} GB."
        )
    else:
        lines += [f"- {w}" for w in report.warnings] or ["- One or more checks failed (see above)."]
        if report.warnings and not all(ok for _, ok, _ in report.checks):
            lines.append("- One or more checks failed (see above).")
    if report.sample_answer:
        lines += ["", "## Sample answer output", "", "```json", report.sample_answer.strip(), "```"]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="path to config.yaml (default: repository root)")
    parser.add_argument("--no-write", action="store_true", help="print only, do not write docs/benchmarks.md")
    args = parser.parse_args(argv)
    config = load_config(args.config)
    report = asyncio.run(run(config, write=not args.no_write))
    print(render(report, config))
    if not args.no_write:
        print(f"Written to {REPORT_PATH}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
