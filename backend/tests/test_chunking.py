"""Chunking (spec section 4 step 5-6), sentence splitting, DOCX pages, number parsing."""

from __future__ import annotations

import io

import pytest
from docx import Document

from app.ingestion.chunk import chunk_text
from app.ingestion.extract import Block, TextDoc, extract, flatten_json
from app.ingestion.tables import infer_type, parse_number
from app.text import estimate_tokens, split_sentences

from .conftest import make_config


def long_section(n_sentences: int, prefix: str) -> str:
    return " ".join(f"{prefix} sentence number {i} talks about refunds and returns." for i in range(n_sentences))


def test_chunks_respect_size_and_overlap(tmp_path):
    cfg = make_config(tmp_path, ingestion={"chunk_size_tokens": 100, "chunk_overlap_tokens": 20})
    doc = TextDoc("Policy", "md", [Block("Refunds", "heading", section="Refunds"),
                                   Block(long_section(40, "Alpha"), "para", section="Refunds", line_start=2, line_end=2)])
    chunks = chunk_text(doc, cfg)
    assert len(chunks) > 3
    assert all(estimate_tokens(c.text) <= 100 for c in chunks)
    for a, b in zip(chunks, chunks[1:]):  # about 20 tokens carried over
        last = split_sentences(a.body)[-1]
        assert b.body.startswith(last) and estimate_tokens(last) <= 20


def test_chunks_never_cross_headings_and_carry_title_and_section(tmp_path):
    cfg = make_config(tmp_path)
    doc = TextDoc("Handbook", "pdf", [
        Block("Leave", "heading", page=1, section="Leave"),
        Block("Annual leave is 18 days.", "para", page=1, section="Leave"),
        Block("Travel", "heading", page=2, section="Travel"),
        Block("The daily allowance is INR 2,500.", "para", page=2, section="Travel"),
    ], pages=2)
    chunks = chunk_text(doc, cfg)
    assert [c.text for c in chunks] == ["Handbook > Leave\nAnnual leave is 18 days.",
                                        "Handbook > Travel\nThe daily allowance is INR 2,500."]
    assert [c.source_location for c in chunks] == ["page 1, section Leave", "page 2, section Travel"]


def test_paragraph_split_on_sentences_when_too_long(tmp_path):
    cfg = make_config(tmp_path, ingestion={"chunk_size_tokens": 60, "chunk_overlap_tokens": 0})
    doc = TextDoc("T", "txt", [Block(long_section(12, "Beta"), "para", line_start=1, line_end=1)])
    chunks = chunk_text(doc, cfg)
    assert len(chunks) > 1 and all(estimate_tokens(c.text) <= 60 for c in chunks)
    joined = " ".join(c.body for c in chunks)
    assert joined == long_section(12, "Beta")  # nothing lost, nothing repeated without overlap


def test_docx_page_breaks_give_page_numbers(tmp_path):
    cfg = make_config(tmp_path)
    d = Document()
    d.add_heading("Intro", 1)
    d.add_paragraph("First page text.")
    d.add_page_break()
    d.add_heading("Second", 1)
    d.add_paragraph("Second page text.")
    buf = io.BytesIO()
    d.save(buf)
    doc = extract("docx", "x.docx", buf.getvalue(), cfg)
    pages = {b.text: b.page for b in doc.blocks}
    assert pages["First page text."] == 1 and pages["Second page text."] == 2
    assert doc.pages == 2


def test_flatten_nested_json():
    assert flatten_json({"customer": {"address": {"city": "Chennai"}, "tags": ["a", "b"]}, "vip": True}) == [
        ("customer.address.city", "Chennai"), ("customer.tags[0]", "a"), ("customer.tags[1]", "b"), ("vip", "true")]


def test_sentence_split_keeps_clause_labels_and_abbreviations():
    text = "Clause 4.2. Daily allowance. The allowance is INR 2,500 per day. Dr. Smith approves it. Rule SCC-11. No gifts."
    assert split_sentences(text) == [
        "Clause 4.2. Daily allowance.", "The allowance is INR 2,500 per day.", "Dr. Smith approves it.",
        "Rule SCC-11. No gifts."]


@pytest.mark.parametrize("raw,expected", [("1,200", 1200), ("1200", 1200), ("4.5", 4.5), (42, 42),
                                          ("007", None), ("Shirt", None), (True, None), ("1,20", None)])
def test_parse_number(raw, expected):
    assert parse_number(raw) == expected


def test_infer_type():
    assert infer_type(["1", "2", None]) == "INTEGER"
    assert infer_type(["1", "2.5"]) == "REAL"
    assert infer_type(["1", "x"]) == "TEXT"
    assert infer_type([None, ""]) == "TEXT"


@pytest.mark.parametrize("meta,expected", [("(anonymous)", None), ("Microsoft Word - policy.docx", None),
                                           ("untitled", None), ("Employee Handbook", "Employee Handbook")])
def test_placeholder_pdf_titles_are_ignored(meta, expected):
    from app.ingestion.extract import _real_title

    assert _real_title(meta) == expected
