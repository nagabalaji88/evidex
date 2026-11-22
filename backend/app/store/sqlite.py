"""SQLite app database: documents, versions, chunks, table schemas, upload log, meta.

SQLite is the source of truth for which version is active. The atomic version switch
(spec section 4) is a single SQLite transaction; the vector store mirrors the status
right after, and every search also filters on the active version ids from here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    file_type  TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS versions (
    id              INTEGER PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    version         INTEGER NOT NULL,
    filename        TEXT NOT NULL,
    title           TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    department      TEXT NOT NULL,
    owner           TEXT NOT NULL,
    uploaded_at     TEXT NOT NULL,
    activated_at    TEXT,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'active', 'inactive')),
    path            TEXT NOT NULL CHECK (path IN ('text', 'table')),
    pages           INTEGER,
    sections        INTEGER NOT NULL DEFAULT 0,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    table_rows      INTEGER NOT NULL DEFAULT 0,
    embedding_model TEXT,
    original_path   TEXT NOT NULL,
    UNIQUE (document_id, version)
);
CREATE INDEX IF NOT EXISTS ix_versions_sha ON versions(sha256);

CREATE TABLE IF NOT EXISTS chunks (
    id              TEXT PRIMARY KEY,
    version_id      INTEGER NOT NULL REFERENCES versions(id),
    document_id     INTEGER NOT NULL,
    ord             INTEGER NOT NULL,
    doc_name        TEXT NOT NULL,
    version         INTEGER NOT NULL,
    page            INTEGER,
    section         TEXT,
    source_location TEXT NOT NULL,
    text            TEXT NOT NULL,   -- what is embedded and keyword-indexed (header + body)
    body            TEXT NOT NULL,   -- the chunk without the title/section header
    status          TEXT NOT NULL CHECK (status IN ('pending', 'active', 'inactive'))
);
CREATE INDEX IF NOT EXISTS ix_chunks_version ON chunks(version_id);
CREATE INDEX IF NOT EXISTS ix_chunks_status ON chunks(status);

CREATE TABLE IF NOT EXISTS table_schemas (
    id          INTEGER PRIMARY KEY,
    version_id  INTEGER NOT NULL REFERENCES versions(id),
    document_id INTEGER NOT NULL,
    file        TEXT NOT NULL,
    sheet       TEXT,
    table_name  TEXT NOT NULL UNIQUE,
    columns     TEXT NOT NULL,   -- JSON list of {"name", "type"}
    row_count   INTEGER NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('pending', 'active', 'inactive'))
);

CREATE TABLE IF NOT EXISTS uploads (
    id          INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL,
    department  TEXT,
    owner       TEXT,
    size_bytes  INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL,
    result      TEXT NOT NULL CHECK (result IN ('indexed', 'duplicate', 'rejected', 'failed')),
    message     TEXT NOT NULL,
    version_id  INTEGER,
    steps       TEXT NOT NULL    -- JSON list of the streamed events
);
"""

TABLE_PREFIX = "tbl_"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE ... COMMIT; rolls back on any exception."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            self.conn.execute("COMMIT")

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- meta ----------------------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
        sql = "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        if conn is not None:
            conn.execute(sql, (key, value))
        else:
            with self._lock:
                self.conn.execute(sql, (key, value))

    def bump_generation(self, conn: sqlite3.Connection) -> None:
        """Called inside every version switch; caches (keyword index) rebuild when it changes."""
        current = int(self.get_meta("index_generation") or 0)
        self.set_meta("index_generation", str(current + 1), conn)

    def generation(self) -> int:
        return int(self.get_meta("index_generation") or 0)

    # -- lookups -------------------------------------------------------------------------

    def find_by_sha(self, sha256: str) -> sqlite3.Row | None:
        return self.one(
            "SELECT v.*, d.name AS doc_name FROM versions v JOIN documents d ON d.id = v.document_id "
            "WHERE v.sha256 = ? AND v.status IN ('active', 'inactive') ORDER BY v.id LIMIT 1",
            (sha256,),
        )

    def active_version_ids(self) -> list[int]:
        return [r["id"] for r in self.query("SELECT id FROM versions WHERE status = 'active'")]

    def active_version(self, document_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM versions WHERE document_id = ? AND status = 'active'", (document_id,))

    def active_chunks(self) -> list[sqlite3.Row]:
        return self.query(
            "SELECT c.* FROM chunks c JOIN versions v ON v.id = c.version_id "
            "WHERE c.status = 'active' AND v.status = 'active' ORDER BY c.version_id, c.ord"
        )

    def chunks_for_version(self, version_id: int) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM chunks WHERE version_id = ? ORDER BY ord", (version_id,))

    def chunks_by_ids(self, ids: list[str]) -> dict[str, sqlite3.Row]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {r["id"]: r for r in self.query(f"SELECT * FROM chunks WHERE id IN ({marks})", tuple(ids))}

    def table_schemas(self, version_id: int) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM table_schemas WHERE version_id = ? ORDER BY id", (version_id,))
        return [{**dict(r), "columns": json.loads(r["columns"])} for r in rows]

    def active_table_schemas(self) -> list[dict[str, Any]]:
        rows = self.query("SELECT * FROM table_schemas WHERE status = 'active' ORDER BY id")
        return [{**dict(r), "columns": json.loads(r["columns"])} for r in rows]

    def log_upload(self, **fields: Any) -> int:
        fields.setdefault("uploaded_at", now_iso())
        fields["steps"] = json.dumps(fields.get("steps", []))
        cols = ",".join(fields)
        marks = ",".join("?" * len(fields))
        with self._lock:
            cur = self.conn.execute(f"INSERT INTO uploads({cols}) VALUES({marks})", tuple(fields.values()))
            return int(cur.lastrowid)


def open_database(config) -> Database:
    return Database(config.sqlite_path)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
