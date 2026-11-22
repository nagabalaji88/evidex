"""Run the 50-case evaluation (spec section 7) and write eval/report.md.

    backend\\.venv\\Scripts\\python eval\\run_eval.py                 # main run with config.yaml
    backend\\.venv\\Scripts\\python eval\\run_eval.py --compare       # + reranker on/off x final_k 3/5
    backend\\.venv\\Scripts\\python eval\\run_eval.py --compare --write-config
                                                    # record the tuned threshold and the cheapest
                                                    # passing setting in config.yaml

Needs Ollama running with both models. Uses a separate, temporary store: your knowledge
base is never touched. Re-run after any change to the prompt, model, chunking or config.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from app.config_writer import set_config_value  # noqa: E402,F401
from app.evaluation.runner import (REPORT_MD, report_dict, run_suite, save_report,  # noqa: E402
                                   threshold_sweep)
from app.health import RerankerState  # noqa: E402
from app.reranker import load_reranker  # noqa: E402


async def main_async(args) -> int:
    config = load_config(args.config)
    state = RerankerState.load(config.model_copy(update={"reranker": config.reranker.model_copy(
        update={"enabled": True})}), load_reranker)
    if state.reranker is None and (config.reranker.enabled or args.compare):
        print(f"Reranker failed to load: {state.error}")
        return 2

    def progress(n, total, case):
        print(f"  test {n} of {total} · {case['category']} · {case['question'][:70]}", flush=True)

    print(f"Main run ({'reranker on' if config.reranker.enabled else 'reranker off'}, "
          f"final_k {config.retrieval.final_k}) ...")
    main = await run_suite(config, label="config.yaml", reranker_obj=state.reranker, progress=progress)
    comparisons = []
    if args.compare:
        for rer in (True, False):
            for k in (3, 5):
                if rer == config.reranker.enabled and k == config.retrieval.final_k:
                    continue
                label = f"reranker {'on' if rer else 'off'}, final_k {k}"
                print(f"Comparison run: {label} ...")
                cfg = config.model_copy(update={
                    "reranker": config.reranker.model_copy(update={"enabled": rer}),
                    "retrieval": config.retrieval.model_copy(update={"final_k": k})})
                comparisons.append(await run_suite(cfg, label=label, reranker_obj=state.reranker, progress=progress))
    threshold = config.retrieval.relevance_threshold if config.reranker.enabled else config.retrieval.vector_threshold
    sweep = threshold_sweep(main.cases, threshold)
    rep = report_dict(main, comparisons, sweep)
    save_report(config, rep)
    m = main.metrics
    print(f"\n{m['passed']} of {m['total']} passed. Report: {REPORT_MD}")
    if args.write_config:
        path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
        if sweep["recommended"] is not None:
            key = "relevance_threshold" if config.reranker.enabled else "vector_threshold"
            set_config_value(path, "retrieval", key, sweep["recommended"])
            print(f"Recorded retrieval.{key} = {sweep['recommended']} in {path}")
        cheap = rep["cheapest_passing"]
        if args.compare and cheap:
            set_config_value(path, "reranker", "enabled", cheap["settings"]["reranker"])
            set_config_value(path, "retrieval", "final_k", cheap["settings"]["final_k"])
            print(f"Recorded the cheapest passing setting ({cheap['label']}) in {path}. Re-run to confirm.")
    return 0 if all(v is not False for v in m["targets_met"].values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Evidex evaluation.")
    parser.add_argument("--config", help="path to config.yaml (default: repository root)")
    parser.add_argument("--compare", action="store_true", help="also run reranker on/off x final_k 3/5")
    parser.add_argument("--write-config", action="store_true",
                        help="record the tuned threshold (and, with --compare, the cheapest passing setting)")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
