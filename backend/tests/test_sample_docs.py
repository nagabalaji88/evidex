"""eval/sample_docs meets spec section 7: at least one PDF, one DOCX, one CSV, a v1/v2 pair
of the same document differing on at least 5 facts, an empty file and a scanned PDF."""

from __future__ import annotations

from pathlib import Path

from docx import Document
from pypdf import PdfReader

from app.config import REPO_ROOT

DOCS = REPO_ROOT / "eval" / "sample_docs"


def test_required_file_types_present():
    suffixes = {p.suffix.lower() for p in DOCS.rglob("*") if p.is_file() and "broken" not in p.parts}
    assert {".pdf", ".docx", ".csv", ".xlsx", ".json", ".txt", ".md"} <= suffixes


def test_text_pdf_has_text_layer_and_several_pages():
    reader = PdfReader(DOCS / "employee_handbook.pdf")
    assert len(reader.pages) >= 3
    assert all(page.extract_text().strip() for page in reader.pages)


def _paragraphs(path: Path) -> list[str]:
    return [p.text for p in Document(str(path)).paragraphs if p.text.strip()]


def test_version_pair_same_name_differs_on_at_least_5_facts():
    v1 = DOCS / "versions" / "v1" / "refund_policy.docx"
    v2 = DOCS / "versions" / "v2" / "refund_policy.docx"
    assert v1.name == v2.name
    p1, p2 = _paragraphs(v1), _paragraphs(v2)
    assert len(p1) == len(p2)
    assert sum(a != b for a, b in zip(p1, p2)) >= 5


def test_broken_files():
    assert (DOCS / "broken" / "empty.txt").stat().st_size == 0
    scanned = PdfReader(DOCS / "broken" / "scanned_notice.pdf")
    assert all(not page.extract_text().strip() for page in scanned.pages)


def test_prompt_injection_document_present():
    text = (DOCS / "delivery_partner_faq.txt").read_text(encoding="utf-8").lower()
    assert "ignore all of your previous rules" in text
