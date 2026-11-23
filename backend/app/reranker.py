"""Cross-encoder reranker (spec section 2): runs in Python via sentence-transformers.

Loaded once at start-up when reranker.enabled is true. CPU threads are limited from
config so the reranker does not starve the answer model.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from .config import Config


class Reranker(Protocol):
    model_name: str

    def score(self, question: str, passages: Sequence[str]) -> list[float]: ...


class CrossEncoderReranker:
    def __init__(self, config: Config):
        try:
            import torch
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError(
                "the local reranker packages are not installed. Switch the reranker off in Settings, or run: "
                "cd backend; .venv\\Scripts\\python -m pip install -r requirements-local.txt") from exc

        rc = config.reranker
        torch.set_num_threads(rc.cpu_threads)
        self.model_name = rc.model
        # max_length truncates each question-passage pair (spec section 5: 512 tokens).
        # Sigmoid is set explicitly so scores are always 0..1, the scale the thresholds use.
        self._model = CrossEncoder(
            rc.model,
            max_length=rc.max_pair_tokens,
            device="cpu",
            activation_fn=torch.nn.Sigmoid(),
        )

    def score(self, question: str, passages: Sequence[str]) -> list[float]:
        """Score all pairs in one batch; returns relevance scores in 0..1."""
        if not passages:
            return []
        scores = self._model.predict([(question, p) for p in passages], batch_size=len(passages))
        return [float(s) for s in scores]


def load_reranker(config: Config) -> Reranker:
    return CrossEncoderReranker(config)
