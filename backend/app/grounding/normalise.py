"""Normalisation for the number and name check (spec section 6).

Numbers: drop thousands separators, currency symbols and trailing zeros (₹1,200.00 = 1200).
Dates: parse both sides to YYYY-MM-DD (12 Mar 2026 = 2026-03-12).
Names: match if one contains the other (Smith matches Dr. Smith).
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
            r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), ("y", "m", "d")),
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH_RE + r",?\s+(\d{4})\b", re.I), ("d", "mon", "y")),
    (re.compile(r"\b" + _MONTH_RE + r"\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", re.I), ("mon", "d", "y")),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), ("d", "m", "y")),  # Indian/UK day-first
]

_CURRENCY = r"(?:₹|\$|€|£|rs\.?\s?|inr\s?|usd\s?)"
_NUMBER = re.compile(r"(?<![\w.,])" + _CURRENCY + r"?(-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?)(?![\w]|,\d)", re.I)
# Indian grouping (1,86,00,000) and western (1,860,000) are both accepted.
_GROUPED = re.compile(r"^-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?$")


def normalise_number(raw: str) -> str | None:
    s = re.sub(_CURRENCY, "", raw.strip(), flags=re.I).strip()
    if "," in s:
        if not _GROUPED.match(s):
            return None
        s = s.replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):
        return None
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    s = s.lstrip("0") or "0" if not s.startswith("-") else "-" + (s[1:].lstrip("0") or "0")
    if s.startswith("."):
        s = "0" + s
    return s


def find_dates(text: str) -> tuple[set[str], str]:
    """ISO dates found in text, and the text with those dates blanked out."""
    found: set[str] = set()
    out = text
    for pat, order in _DATE_PATTERNS:
        def repl(m):
            parts = dict(zip(order, m.groups()))
            try:
                month = int(parts["m"]) if "m" in parts else _MONTHS[parts["mon"][:3].lower()]
                found.add(date(int(parts["y"]), month, int(parts["d"])).isoformat())
            except (ValueError, KeyError):
                return m.group(0)
            return " "
        out = pat.sub(repl, out)
    return found, out


def find_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _NUMBER.finditer(text):
        n = normalise_number(m.group(1))
        if n is not None:
            out.add(n)
    return out


def numbers_and_dates(text: str) -> tuple[set[str], set[str]]:
    dates, rest = find_dates(text)
    return find_numbers(rest), dates


_NAME = re.compile(r"\b(?:[A-Z][a-z]+\.?|[A-Z]{2,}[a-z]*)(?:\s+(?:[A-Z][a-z]+\.?|[A-Z]{2,}[a-z]*))*")
_CODE = re.compile(r"\b[A-Za-z]+(?:-[A-Za-z0-9]+)+\b|\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_SENTENCE_STARTERS = {
    "the", "a", "an", "this", "that", "these", "those", "it", "they", "yes", "no", "only", "all", "each", "every",
    "some", "any", "if", "when", "after", "before", "for", "in", "on", "at", "to", "from", "with", "store", "customers",
    "employees", "suppliers", "staff", "refunds", "orders", "there", "you", "we", "both", "under", "per", "up",
}


def find_names(text: str) -> set[str]:
    """Proper names and codes: capitalised word runs not at the sentence start, codes, emails."""
    names: set[str] = {m.group(0) for m in _CODE.finditer(text)}
    text = _CODE.sub(lambda m: "x" * len(m.group(0)), text)  # codes are matched whole, not in parts
    for m in _NAME.finditer(text):
        name = m.group(0).strip()
        words = name.split()
        at_start = m.start() == 0 or re.search(r"[.!?:]\s*$", text[: m.start()])
        if at_start:
            if words[0].lower().rstrip(".") in _SENTENCE_STARTERS or len(words) == 1:
                words = words[1:]
        name = " ".join(words).strip().rstrip(".")
        if name and not re.fullmatch(_MONTH_RE, name, re.I):
            names.add(name)
    return names


def name_in_source(name: str, source_text: str, source_names: set[str]) -> bool:
    if name.lower() in source_text.lower():
        return True
    low = name.lower()
    return any(s.lower() in low or low in s.lower() for s in source_names if len(s) > 2)
