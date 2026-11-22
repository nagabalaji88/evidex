"""The table path (spec section 5): table pick, SQL prompt, validation and a read-only run.

Validation in code: a single SELECT statement only; PRAGMA, ATTACH and every write or DDL
keyword blocked; only real table and column names of the candidate tables. The query runs
on a read-only SQLite connection with sql.timeout_seconds and at most sql.max_rows rows.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..store.sqlite import quote_ident

NO_ROWS_MESSAGE = "No rows match this filter in the provided data."

_BLOCKED = ["PRAGMA", "ATTACH", "DETACH", "INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "REPLACE",
            "VACUUM", "REINDEX", "TRUNCATE", "GRANT", "REVOKE", "UPSERT", "MERGE", "ANALYZE", "SAVEPOINT",
            "RELEASE", "BEGIN", "COMMIT", "ROLLBACK", "LOAD_EXTENSION", "WRITEFILE", "READFILE"]
_BLOCKED_RE = re.compile(r"\b(" + "|".join(_BLOCKED) + r")\b", re.IGNORECASE)


class SqlRejected(ValueError):
    def __init__(self, check: str, message: str):
        super().__init__(message)
        self.check, self.message = check, message


@dataclass
class SqlRun:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    ms: int
    checks: list[dict[str, Any]] = field(default_factory=list)


def _strip_strings(sql: str) -> str:
    """Blank out string literals and comments so keyword checks only see SQL."""
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)


def describe_tables(conn: sqlite3.Connection, schemas: list[dict[str, Any]], sample_rows: int = 3) -> str:
    """Column names and types plus sample rows for each candidate table (for the SQL prompt)."""
    parts = []
    for s in schemas:
        cols = ", ".join(f"{quote_ident(c['name'])} {c['type']}" for c in s["columns"])
        cur = conn.execute(f"SELECT * FROM {quote_ident(s['table_name'])} LIMIT {int(sample_rows)}")
        rows = [list(r) for r in cur.fetchall()]
        where = s["file"] + (f", sheet {s['sheet']}" if s.get("sheet") else "")
        parts.append(f"Table {quote_ident(s['table_name'])} (from {where}, {s['row_count']} rows)\n"
                     f"Columns: {cols}\nSample rows: {rows}")
    return "\n\n".join(parts)


def validate_sql(sql: str, schemas: list[dict[str, Any]], db_path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Return the cleaned SQL and the passed checks, or raise SqlRejected."""
    sql = (sql or "").strip().rstrip(";").strip()
    bare = _strip_strings(sql)
    checks = []
    if not sql or ";" in bare:
        raise SqlRejected("One statement", "Only a single SQL statement is allowed.")
    if not re.match(r"^\s*SELECT\b", bare, re.IGNORECASE):
        raise SqlRejected("One statement", "Only a SELECT statement is allowed.")
    checks.append({"name": "One statement", "passed": True, "detail": "A single SELECT"})
    blocked = _BLOCKED_RE.search(bare)
    if blocked:
        raise SqlRejected("No changes", f"{blocked.group(1).upper()} is not allowed.")
    checks.append({"name": "No changes", "passed": True, "detail": "No INSERT, UPDATE, DELETE, PRAGMA or ATTACH"})
    # Real names only: compile the statement with an authorizer that allows reading the
    # candidate tables' real columns and nothing else. A missing column fails to compile.
    allowed = {s["table_name"]: {c["name"] for c in s["columns"]} for s in schemas}
    conn = _readonly(db_path)
    denied: list[str] = []

    def authorizer(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if arg1 in allowed and (arg2 in allowed[arg1] or arg2 == ""):
                return sqlite3.SQLITE_OK
            denied.append(f"{arg1}.{arg2}")
            return sqlite3.SQLITE_DENY
        denied.append(f"action {action}")
        return sqlite3.SQLITE_DENY

    conn.set_authorizer(authorizer)
    try:
        conn.execute(f"EXPLAIN {sql}")
    except sqlite3.Error as exc:
        msg = str(exc)
        if denied:
            msg = f"Only the candidate tables and their columns may be used (refused: {', '.join(denied[:3])})."
        raise SqlRejected("Real names", msg) from exc
    finally:
        conn.close()
    checks.append({"name": "Real names", "passed": True, "detail": "Every table and column exists"})
    return sql, checks


def run_sql(sql: str, db_path: Path, *, timeout_seconds: float, max_rows: int) -> SqlRun:
    conn = _readonly(db_path)
    deadline = time.perf_counter() + timeout_seconds
    conn.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 1000)
    t = time.perf_counter()
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(max_rows + 1)
        columns = [d[0] for d in cur.description or []]
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise SqlRejected("Limits", f"The query took longer than {timeout_seconds:g} s.") from exc
        raise SqlRejected("Run", str(exc)) from exc
    finally:
        conn.close()
    truncated = len(rows) > max_rows
    return SqlRun(sql, columns, [list(r) for r in rows[:max_rows]], truncated, int((time.perf_counter() - t) * 1000))


def _readonly(db_path: Path) -> sqlite3.Connection:
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only = ON")
    # Without this, SQLite reads "Profit" (a missing column) as the string 'Profit'.
    conn.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
    conn.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DDL, False)
    return conn
