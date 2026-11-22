"""Step 2: extract text with page numbers and section headings (spec section 4).

Text path (PDF, DOCX, TXT, MD, nested JSON) -> TextDoc made of Blocks.
Table path (CSV, XLSX, JSON list of records) -> TableDoc made of TableData.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import PurePath
from statistics import median
from typing import Any

from ..config import Config
from .errors import SCANNED_PDF_MESSAGE, IngestError


@dataclass
class Block:
    text: str
    kind: str = "para"  # heading | para | line
    page: int | None = None
    section: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    json_path: str | None = None


@dataclass
class TextDoc:
    title: str
    file_type: str
    blocks: list[Block]
    pages: int | None = None

    @property
    def sections(self) -> list[str]:
        seen: dict[str, None] = {}
        for b in self.blocks:
            if b.section:
                seen.setdefault(b.section, None)
        return list(seen)


@dataclass
class TableData:
    sheet: str | None
    columns: list[str]
    rows: list[list[Any]]
    row_numbers: list[int] = field(default_factory=list)  # spreadsheet row / record index per row


@dataclass
class TableDoc:
    title: str
    file_type: str
    tables: list[TableData]


def default_title(filename: str) -> str:
    return PurePath(filename).stem.replace("_", " ").replace("-", " ").strip().title() or filename


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract(kind: str, filename: str, data: bytes, config: Config) -> TextDoc | TableDoc:
    fn = {"pdf": _pdf, "docx": _docx, "txt": _txt, "md": _md, "csv": _csv, "xlsx": _xlsx, "json": _json}[kind]
    try:
        doc = fn(filename, data, config)
    except IngestError:
        raise
    except Exception as exc:  # corrupt or unreadable file
        raise IngestError("unreadable", f"{filename} could not be read: {type(exc).__name__}: {exc}") from exc
    if isinstance(doc, TextDoc) and not any(b.text.strip() for b in doc.blocks if b.kind != "heading"):
        raise IngestError("no_text", f"No text was found in {filename}.")
    return doc


# -- PDF ----------------------------------------------------------------------------------


def _pdf(filename: str, data: bytes, config: Config) -> TextDoc:
    import pdfplumber

    pages: list[list[tuple[str, float, bool, float, float]]] = []
    sizes: Counter = Counter()
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        meta_title = (pdf.metadata or {}).get("Title")
        n_pages = len(pdf.pages)
        for page in pdf.pages:
            out = []
            for ln in page.extract_text_lines(return_chars=True, strip=True):
                chars = [c for c in ln["chars"] if c["text"].strip()]
                if not chars:
                    continue
                size = round(median(c["size"] for c in chars), 1)
                bold = all("bold" in (c.get("fontname") or "").lower() for c in chars)
                out.append((ln["text"].strip(), size, bold, ln["top"], ln["bottom"]))
                sizes[size] += len(chars)
            pages.append(out)
        if not sizes:
            if not config.ingestion.ocr_enabled:
                raise IngestError("scanned_pdf", SCANNED_PDF_MESSAGE)
            return _pdf_ocr(filename, pdf, meta_title)

    body = sizes.most_common(1)[0][0]
    blocks: list[Block] = []
    section: str | None = None
    for pno, lines in enumerate(pages, 1):
        para: list[str] = []
        prev_bottom: float | None = None

        def flush() -> None:
            if para:
                blocks.append(Block(_join_pdf_lines(para), "para", pno, section))
                para.clear()

        for text, size, bold, top, bottom in lines:
            heading = (size >= body * 1.15 or (bold and size >= body)) and len(text.split()) <= 15
            if heading:
                flush()
                # A heading wrapped over two lines continues the previous heading.
                if blocks and blocks[-1].kind == "heading" and blocks[-1].page == pno and prev_bottom is not None \
                        and top - prev_bottom < (bottom - top) * 0.6:
                    blocks[-1].text += " " + text
                    section = blocks[-1].text
                    blocks[-1].section = section
                else:
                    section = text
                    blocks.append(Block(text, "heading", pno, section))
                prev_bottom = bottom
                continue
            if para and prev_bottom is not None and top - prev_bottom > (bottom - top) * 0.6:
                flush()
            para.append(text)
            prev_bottom = bottom
        flush()

    first_heading = next((b.text for b in blocks if b.kind == "heading"), None)
    title = _real_title(meta_title) or first_heading or default_title(filename)
    return TextDoc(title=title, file_type="pdf", blocks=blocks, pages=n_pages)


_PLACEHOLDER_TITLES = {"(anonymous)", "anonymous", "untitled", "(untitled)", "document", "title"}


def _real_title(meta_title: str | None) -> str | None:
    """PDF metadata titles are often tool placeholders ("(anonymous)", "Microsoft Word - x.docx")."""
    t = (meta_title or "").strip()
    t = re.sub(r"^Microsoft (Word|PowerPoint|Excel) - ", "", t)
    if not t or t.lower() in _PLACEHOLDER_TITLES or re.fullmatch(r"[\w.-]+\.(docx?|pdf|txt)", t, re.I):
        return None
    return t


def _join_pdf_lines(lines: list[str]) -> str:
    out = lines[0]
    for ln in lines[1:]:
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln  # re-join a hyphenated word
        else:
            out += " " + ln
    return out


def _pdf_ocr(filename: str, pdf, meta_title: str | None) -> TextDoc:
    """Optional OCR (ingestion.ocr_enabled). Needs pytesseract and the Tesseract program."""
    try:
        import pytesseract
    except ImportError as exc:
        raise IngestError(
            "ocr_unavailable",
            "OCR is enabled in config, but no OCR engine is installed (pip install pytesseract, then install Tesseract).",
        ) from exc
    blocks = []
    for pno, page in enumerate(pdf.pages, 1):
        text = pytesseract.image_to_string(page.to_image(resolution=300).original)
        for para in re.split(r"\n\s*\n", text):
            if para.strip():
                blocks.append(Block(" ".join(para.split()), "para", pno, None))
    if not blocks:
        raise IngestError("scanned_pdf", SCANNED_PDF_MESSAGE)
    return TextDoc(title=_real_title(meta_title) or default_title(filename), file_type="pdf", blocks=blocks,
                   pages=len(pdf.pages))


# -- DOCX ---------------------------------------------------------------------------------

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(filename: str, data: bytes, config: Config) -> TextDoc:
    """DOCX has no stored page numbers. Pages are counted from the page breaks Word saves
    (w:lastRenderedPageBreak) when present, else from explicit page breaks; page 1 if none."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(io.BytesIO(data))
    body = doc.element.body
    use_rendered = next(body.iter(f"{_W}lastRenderedPageBreak"), None) is not None

    def breaks(el) -> tuple[int, int]:
        """(page breaks before the first text, page breaks after it) inside one element."""
        before = after = 0
        seen_text = False
        for node in el.iter():
            if node.tag == f"{_W}t" and (node.text or "").strip():
                seen_text = True
            is_break = (
                node.tag == f"{_W}lastRenderedPageBreak"
                if use_rendered
                else (node.tag == f"{_W}br" and node.get(f"{_W}type") == "page")
                or node.tag == f"{_W}pageBreakBefore"
            )
            if is_break:
                if seen_text:
                    after += 1
                else:
                    before += 1
        return before, after

    blocks: list[Block] = []
    page, section, title = 1, None, None
    for el in body.iterchildren():
        if el.tag not in (f"{_W}p", f"{_W}tbl"):
            continue
        before, after = breaks(el)
        if blocks:  # a break before the very first content does not start page 2
            page += before
        if el.tag == f"{_W}p":
            p = Paragraph(el, doc)
            text = p.text.strip()
            style = (p.style.name if p.style is not None else "") or ""
            if text:
                if style == "Title" or style.startswith("Heading"):
                    if style == "Title" and title is None:
                        title = text
                    section = text
                    blocks.append(Block(text, "heading", page, section))
                else:
                    blocks.append(Block(text, "para", page, section))
        else:
            table = Table(el, doc)
            for row in table.rows:
                cells: list[str] = []
                for c in row.cells:
                    t = c.text.strip()
                    if t and (not cells or cells[-1] != t):  # merged cells repeat their text
                        cells.append(t)
                if cells:
                    blocks.append(Block(" | ".join(cells), "line", page, section))
        page += after

    core_title = (doc.core_properties.title or "").strip()
    pages = max((b.page or 1 for b in blocks), default=1)
    return TextDoc(title=core_title or title or default_title(filename), file_type="docx", blocks=blocks, pages=pages)


# -- TXT and MD ---------------------------------------------------------------------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _paragraphs(lines: list[str]) -> list[tuple[int, int, list[str]]]:
    """Split lines into paragraphs on blank lines -> (first line no, last line no, lines)."""
    out, cur, start = [], [], 0
    for i, line in enumerate(lines, 1):
        if line.strip():
            if not cur:
                start = i
            cur.append(line.rstrip())
        elif cur:
            out.append((start, i - 1, cur))
            cur = []
    if cur:
        out.append((start, len(lines), cur))
    return out


def _txt(filename: str, data: bytes, config: Config) -> TextDoc:
    lines = decode_text(data).splitlines()
    blocks = [Block("\n".join(p), "para", None, None, a, b) for a, b, p in _paragraphs(lines)]
    return TextDoc(title=default_title(filename), file_type="txt", blocks=blocks)


def _md(filename: str, data: bytes, config: Config) -> TextDoc:
    lines = decode_text(data).splitlines()
    blocks: list[Block] = []
    section, title, in_fence = None, None, False
    para: list[str] = []
    start = 0

    def flush(end: int) -> None:
        if para:
            blocks.append(Block("\n".join(para), "para", None, section, start, end))
            para.clear()

    for i, line in enumerate(lines, 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else _MD_HEADING.match(line)
        if m:
            flush(i - 1)
            section = m.group(2).strip()
            if title is None and len(m.group(1)) == 1:
                title = section
            blocks.append(Block(section, "heading", None, section, i, i))
        elif line.strip() or in_fence:
            if not para:
                start = i
            para.append(line.rstrip())
        else:
            flush(i - 1)
    flush(len(lines))
    return TextDoc(title=title or default_title(filename), file_type="md", blocks=blocks)


# -- Tables: CSV, XLSX --------------------------------------------------------------------


def clean_columns(header: list[Any]) -> list[str]:
    """Keep the original column names; fill blanks and de-duplicate."""
    out: list[str] = []
    for i, h in enumerate(header, 1):
        name = str(h).strip() if h is not None and str(h).strip() else f"column_{i}"
        base, n = name, 2
        while name in out:
            name = f"{base}_{n}"
            n += 1
        out.append(name)
    return out


def _cell(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.isoformat(sep=" ")
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, str):
        v = v.strip()
        return v if v else None
    return v


def _table_from_rows(sheet: str | None, rows: list[tuple[int, list[Any]]]) -> TableData | None:
    rows = [(n, [_cell(v) for v in r]) for n, r in rows]
    rows = [(n, r) for n, r in rows if any(v is not None for v in r)]
    if not rows:
        return None
    width = max(max((i + 1 for i, v in enumerate(r) if v is not None), default=0) for _, r in rows)
    header_no, header = rows[0]
    data = [(n, (r + [None] * width)[:width]) for n, r in rows[1:]]
    if not data:
        return None
    return TableData(sheet, clean_columns((header + [None] * width)[:width]), [r for _, r in data], [n for n, _ in data])


def _csv(filename: str, data: bytes, config: Config) -> TableDoc:
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [(i, list(r)) for i, r in enumerate(csv.reader(io.StringIO(text), dialect), 1)]
    table = _table_from_rows(None, rows)
    if table is None:
        raise IngestError("no_rows", f"{filename} has a header but no data rows.")
    return TableDoc(title=default_title(filename), file_type="csv", tables=[table])


def _xlsx(filename: str, data: bytes, config: Config) -> TableDoc:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    tables = []
    for ws in wb.worksheets:
        rows = [(i, list(r)) for i, r in enumerate(ws.iter_rows(values_only=True), 1)]
        t = _table_from_rows(ws.title, rows)
        if t is not None:
            tables.append(t)
    wb.close()
    if not tables:
        raise IngestError("no_rows", f"{filename} has no sheet with a header and data rows.")
    title = (wb.properties.title or "").strip() if wb.properties else ""
    return TableDoc(title=title or default_title(filename), file_type="xlsx", tables=tables)


# -- JSON ---------------------------------------------------------------------------------


def _json(filename: str, data: bytes, config: Config) -> TextDoc | TableDoc:
    try:
        obj = json.loads(decode_text(data))
    except json.JSONDecodeError as exc:
        raise IngestError("unreadable", f"{filename} is not valid JSON: {exc}") from exc
    title = default_title(filename)
    if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
        cols: list[str] = []
        for rec in obj:
            for k in rec:
                if str(k) not in cols:
                    cols.append(str(k))
        rows = [
            [v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v, ensure_ascii=False)
             for v in (rec.get(c) for c in cols)]
            for rec in obj
        ]
        # row_numbers hold the record index, cited as JSON path $[i]
        return TableDoc(title=title, file_type="json", tables=[TableData(None, clean_columns(cols), rows,
                                                                         list(range(len(rows))))])
    blocks = [
        Block(f"{path}: {value}", "line", None, _json_section(path), json_path="$." + path if path else "$")
        for path, value in flatten_json(obj)
    ]
    return TextDoc(title=title, file_type="json", blocks=blocks)


def flatten_json(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """{"customer": {"address": {"city": "Chennai"}}} -> [("customer.address.city", "Chennai")]."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += flatten_json(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += flatten_json(v, f"{prefix}[{i}]")
    else:
        value = "null" if obj is None else (str(obj).lower() if isinstance(obj, bool) else str(obj))
        out.append((prefix, value))
    return out


def _json_section(path: str) -> str | None:
    """Group lines by their first two path levels: programme.tiers.gold.x -> programme.tiers."""
    parts = re.split(r"\.(?![^\[]*\])", path)
    return ".".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) == 2 else None)
