"""Follow-up rewriting (spec section 5): a question that depends on chat history is
rewritten into a standalone question, using the last retrieval.history_turns turns."""

from __future__ import annotations

import re

# Openers and pronouns that only make sense with the earlier conversation.
_OPENERS = re.compile(r"^\s*(what about|how about|and|also|what if|same for|or|but|then|so)\b", re.IGNORECASE)
_PRONOUNS = re.compile(r"\b(it|its|they|them|their|theirs|that|those|this|these|there|he|she|him|her|his|hers|"
                       r"the same|the other|ones?)\b", re.IGNORECASE)


def needs_rewrite(question: str, history: list[tuple[str, str | None]]) -> bool:
    if not history:
        return False
    words = re.findall(r"\w+", question)
    return bool(_OPENERS.search(question) or _PRONOUNS.search(question) or len(words) <= 3)


def format_history(history: list[tuple[str, str | None]]) -> str:
    lines = []
    for q, a in history:
        lines.append(f"User: {q}")
        if a:
            lines.append(f"Assistant: {a}")
    return "\n".join(lines)


def fallback_rewrite(question: str, history: list[tuple[str, str | None]]) -> str:
    """Used when the model cannot rewrite: search with the previous question for context."""
    return f"{history[-1][0]} {question}" if history else question
