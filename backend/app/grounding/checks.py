"""Grounding checks (spec section 6), in order:

1. Citation check (always on): every sentence carries IDs that exist in this turn's map.
2. Number and name check (always on): every number, date, amount and proper name in a
   sentence appears in the retrieved chunks after normalisation.
3. Claim check (LLM, grounding.claim_check_enabled, default off).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..answer.sentences import SourceSentence
from .normalise import find_names, name_in_source, numbers_and_dates

_ID_TAG = re.compile(r"\s*\[S\d+\]")


def clean_text(text: str) -> str:
    """Drop any [S4]-style tags the model wrote into the sentence text."""
    return " ".join(_ID_TAG.sub("", text or "").split())


@dataclass
class SentenceVerdict:
    text: str
    ids: list[str]
    passed: bool = True
    failures: list[dict[str, str]] = field(default_factory=list)  # {"check", "detail"}

    def fail(self, check: str, detail: str) -> None:
        self.passed = False
        self.failures.append({"check": check, "detail": detail})


@dataclass
class GroundingReport:
    sentences: list[SentenceVerdict]
    checks: list[dict[str, Any]]  # one summary row per check, for the UI

    @property
    def passed(self) -> list[SentenceVerdict]:
        return [s for s in self.sentences if s.passed]

    @property
    def all_passed(self) -> bool:
        return bool(self.sentences) and all(s.passed for s in self.sentences)

    def failure_summary(self) -> list[str]:
        return [f"\"{s.text}\": {f['check']} - {f['detail']}" for s in self.sentences for f in s.failures]


def citation_check(sentences: list[dict[str, Any]], smap: dict[str, SourceSentence]) -> list[SentenceVerdict]:
    out = []
    for s in sentences:
        ids = [str(i).strip().strip("[]") for i in (s.get("ids") or [])]
        v = SentenceVerdict(clean_text(s.get("text", "")), ids)
        if not v.text:
            v.fail("citation", "empty sentence")
        elif not ids:
            v.fail("citation", "no source sentence ID")
        else:
            unknown = [i for i in ids if i not in smap]
            if unknown:
                v.fail("citation", f"ID {', '.join(unknown)} was not sent to the model")
        out.append(v)
    return out


def number_name_check(verdicts: list[SentenceVerdict], source_text: str) -> None:
    """Check against all chunks sent this turn (the retrieved passages)."""
    src_numbers, src_dates = numbers_and_dates(source_text)
    src_names = find_names(source_text)
    for v in verdicts:
        if not v.passed:
            continue
        numbers, dates = numbers_and_dates(v.text)
        missing_n = sorted(n for n in numbers if n not in src_numbers)
        missing_d = sorted(d for d in dates if d not in src_dates)
        missing_names = sorted(n for n in find_names(v.text) if not name_in_source(n, source_text, src_names))
        if missing_n:
            v.fail("numbers", f"{', '.join(missing_n)} not found in the sources")
        if missing_d:
            v.fail("dates", f"{', '.join(missing_d)} not found in the sources")
        if missing_names:
            v.fail("names", f"{', '.join(missing_names)} not found in the sources")


def summarise(verdicts: list[SentenceVerdict], smap: dict[str, SourceSentence], claim_ran: bool) -> list[dict[str, Any]]:
    def rows(check: str) -> list[str]:
        return [f["detail"] for v in verdicts for f in v.failures if f["check"] == check]

    cited = sorted({i for v in verdicts if v.passed for i in v.ids}, key=lambda x: int(x[1:]))
    out = [{"name": "Source tags", "passed": not rows("citation"),
            "detail": (f"{', '.join(cited)} {'was' if len(cited) == 1 else 'were'} given to the model"
                       if not rows("citation") else "; ".join(rows("citation")))}]
    num_fail = rows("numbers") + rows("dates")
    out.append({"name": "Numbers", "passed": not num_fail,
                "detail": "Every number and date appears in the sources" if not num_fail else "; ".join(num_fail)})
    out.append({"name": "Names", "passed": not rows("names"),
                "detail": "Every name and code appears in the sources" if not rows("names") else "; ".join(rows("names"))})
    if claim_ran:
        out.append({"name": "Claims", "passed": not rows("claim"),
                    "detail": "Every sentence is supported" if not rows("claim") else "; ".join(rows("claim"))})
    return out


def apply_claim_result(verdicts: list[SentenceVerdict], data: dict | None) -> None:
    if not isinstance(data, dict):
        raise ValueError("claim check returned no JSON")
    bad = {clean_text(t).lower() for t in data.get("unsupported_sentences") or []}
    for v in verdicts:
        if v.passed and (v.text.lower() in bad or (data.get("supported") is False and not bad)):
            v.fail("claim", "not supported by the excerpts")
