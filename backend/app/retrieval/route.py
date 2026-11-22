"""Question routing (spec section 5): lookup/explanation vs calculation.

Rules first, with no LLM call: a calculation word AND a column name from a schema record
means calculation; neither means lookup. Only when exactly one of the two is present are
the rules inconclusive, and the LLM is asked for a one-word label. If still unsure, the
text pipeline is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

# Spec section 5: total, sum, count, how many, average, highest, lowest, top (+ plain synonyms).
CALC_PATTERNS = [
    r"total", r"sum", r"count", r"how many", r"number of", r"average", r"mean", r"median",
    r"highest", r"lowest", r"top \d+|top", r"maximum|max", r"minimum|min", r"most", r"least",
    r"rank(?:ing|ed)?", r"bottom \d+",
]
_CALC = re.compile(r"\b(" + "|".join(f"(?:{p})" for p in CALC_PATTERNS) + r")\b", re.IGNORECASE)


@dataclass
class RouteDecision:
    route: str  # "lookup" | "calculation"
    reason: str
    keywords: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)  # candidate table names when calculation
    used_llm: bool = False
    llm_label: str | None = None


def _words(text: str) -> list[str]:
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower())]


def column_words(name: str) -> list[str]:
    """'LeadTimeDays' -> ['lead', 'time', 'day']; 'area_sqft' -> ['area', 'sqft']."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return _words(spaced)


def match_columns(question: str, schemas: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Columns whose every word appears in the question, and the tables they belong to."""
    q = set(_words(question))
    cols, tables = [], []
    for s in schemas:
        hit = False
        for c in s["columns"]:
            words = column_words(c["name"])
            if words and all(w in q for w in words):
                if c["name"] not in cols:
                    cols.append(c["name"])
                hit = True
        if hit:
            tables.append(s["table_name"])
    return cols, tables


def route_by_rules(question: str, schemas: list[dict[str, Any]]) -> RouteDecision | None:
    """A decision, or None when the rules are inconclusive."""
    if not schemas:
        return RouteDecision("lookup", "No tables in the selected documents, so search the text")
    keywords = sorted({m.group(1).lower() for m in _CALC.finditer(question)})
    columns, tables = match_columns(question, schemas)
    if keywords and columns:
        return RouteDecision("calculation", f"Found {', '.join(repr(k) for k in keywords)} plus the column names "
                             f"{', '.join(columns)}", keywords, columns, tables)
    if not keywords and not columns:
        return RouteDecision("lookup", "No calculation words like total, highest or average")
    return None


async def route_question(
    question: str,
    schemas: list[dict[str, Any]],
    ask_label: Callable[[str, list[dict[str, Any]]], Awaitable[str | None]] | None,
) -> RouteDecision:
    decided = route_by_rules(question, schemas)
    if decided is not None:
        return decided
    keywords = sorted({m.group(1).lower() for m in _CALC.finditer(question)})
    columns, tables = match_columns(question, schemas)
    label = await ask_label(question, schemas) if ask_label is not None else None
    if label == "calculation":
        tables = tables or [s["table_name"] for s in schemas]
        return RouteDecision("calculation", "The rules were unsure; the model labelled it a calculation",
                             keywords, columns, tables, used_llm=True, llm_label=label)
    return RouteDecision("lookup", "The rules were unsure; the model labelled it a lookup" if label == "lookup"
                         else "The rules were unsure, so use the text pipeline", keywords, columns,
                         used_llm=ask_label is not None, llm_label=label)
