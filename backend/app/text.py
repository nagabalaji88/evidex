"""Text helpers shared by chunking (M2) and sentence numbering (M4)."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


def estimate_tokens(text: str) -> int:
    """Approximate token count: words plus punctuation marks. Close to BPE/WordPiece
    counts for English prose; used for chunk sizes, which are approximate by spec."""
    return len(_TOKEN.findall(text))


_ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "no", "nos", "rs", "st", "inc", "ltd", "co", "vs", "etc", "e.g", "i.e",
    "approx", "dept", "fig", "jr", "sr", "pvt",
}
# "Clause 4.2." / "Rule SCC-11." / a leading list number "2." are labels, not sentence ends.
_LABEL_END = re.compile(
    r"(?:\b(?:clause|rule|section|article|para|paragraph|step|part|chapter|item)\s+[\w.-]+|^\s*[\w]{1,3}(?:\.\d+)*)\.$",
    re.IGNORECASE,
)
_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9₹$€£])")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking on abbreviations or clause labels."""
    text = " ".join(text.split())
    if not text:
        return []
    out: list[str] = []
    start = 0
    for m in _SPLIT.finditer(text):
        candidate = text[start : m.start()].rstrip()
        last_word = candidate.rsplit(" ", 1)[-1].rstrip(".").lower()
        if last_word in _ABBREVIATIONS or _LABEL_END.search(candidate):
            continue
        out.append(text[start : m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out
