"""Table path (spec section 4): each sheet / CSV / JSON record list becomes a SQLite table
with its original column names, plus a schema record (file, sheet, table name, column
names and types, row count). Multi-sheet Excel files make one table per sheet, all
sharing the file's version."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..store.sqlite import TABLE_PREFIX, quote_ident
from .extract import TableData, TableDoc

_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def parse_number(v: Any) -> int | float | None:
    """'1,200' -> 1200, '4.5' -> 4.5, 42 -> 42; anything else -> None. Bools are not numbers."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if not isinstance(v, str):
        return None
    s = v.strip()
    if _THOUSANDS.match(s):
        s = s.replace(",", "")
    if _INT.match(s):
        # keep codes with leading zeros ("007") as text
        return int(s) if not (len(s.lstrip("-")) > 1 and s.lstrip("-").startswith("0")) else None
    if _FLOAT.match(s):
        return float(s)
    return None


def infer_type(values: list[Any]) -> str:
    present = [v for v in values if v is not None and v != ""]
    if not present:
        return "TEXT"
    nums = [parse_number(v) for v in present]
    if any(n is None for n in nums):
        return "TEXT"
    return "INTEGER" if all(isinstance(n, int) for n in nums) else "REAL"


def _convert(v: Any, typ: str) -> Any:
    if v is None or v == "":
        return None
    if typ in ("INTEGER", "REAL"):
        n = parse_number(v)
        return float(n) if typ == "REAL" else n
    if isinstance(v, bool):
        return "true" if v else "false"
    return v if isinstance(v, str) else str(v)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "sheet"


def table_name(version_id: int, doc_stem: str, sheet: str | None) -> str:
    return f"{TABLE_PREFIX}v{version_id}_{_slug(doc_stem)}" + (f"_{_slug(sheet)}" if sheet else "")


def create_tables(
    conn: sqlite3.Connection, doc: TableDoc, *, version_id: int, document_id: int, filename: str, stem: str
) -> list[dict[str, Any]]:
    """Create and fill one SQLite table per TableData; insert pending schema records.
    Runs inside the caller's transaction."""
    schemas = []
    used: set[str] = set()
    for t in doc.tables:
        name = table_name(version_id, stem, t.sheet)
        base, n = name, 2
        while name in used:
            name = f"{base}_{n}"
            n += 1
        used.add(name)
        columns = _columns(t)
        cols_sql = ", ".join(f"{quote_ident(c['name'])} {c['type']}" for c in columns)
        conn.execute(f"CREATE TABLE {quote_ident(name)} ({cols_sql})")
        marks = ", ".join("?" * len(columns))
        conn.executemany(
            f"INSERT INTO {quote_ident(name)} VALUES ({marks})",
            [[_convert(v, c["type"]) for v, c in zip(row, columns)] for row in t.rows],
        )
        conn.execute(
            "INSERT INTO table_schemas(version_id, document_id, file, sheet, table_name, columns, row_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
            (version_id, document_id, filename, t.sheet, name, json.dumps(columns), len(t.rows)),
        )
        schemas.append({"table_name": name, "sheet": t.sheet, "columns": columns, "row_count": len(t.rows)})
    return schemas


def _columns(t: TableData) -> list[dict[str, str]]:
    return [{"name": c, "type": infer_type([r[i] for r in t.rows])} for i, c in enumerate(t.columns)]


def drop_tables(conn: sqlite3.Connection, version_id: int) -> None:
    for (name,) in conn.execute("SELECT table_name FROM table_schemas WHERE version_id = ?", (version_id,)).fetchall():
        conn.execute(f"DROP TABLE IF EXISTS {quote_ident(name)}")
    conn.execute("DELETE FROM table_schemas WHERE version_id = ?", (version_id,))
