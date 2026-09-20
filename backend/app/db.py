"""SQLite access: one connection per request, PRAGMAs set on every open, and
the SQL half of the analytics exclusion rule.

``CLEAN_PREDICATE`` is the *only* place the exclusion predicate is spelled out
in SQL. Every query feeding trend weight, adaptive TDEE, adherence or
progression appends it via ``clean_where()``; the Python side is
``app.engine.exclusion.clean_rows``.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

CACHE_SIZE_KIB = 64 * 1024  # spec resource budget: 64 MB page cache

CLEAN_PREDICATE = "health_event_id IS NULL"


def clean_where(alias: str | None = None) -> str:
    """`` AND <alias.>health_event_id IS NULL`` for appending to analytics queries."""
    col = f"{alias}.{CLEAN_PREDICATE}" if alias else CLEAN_PREDICATE
    return f" AND {col}"


def connect(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # autocommit; explicit BEGIN where needed. check_same_thread=False because FastAPI
    # may open and close a per-request connection on different threadpool threads;
    # a connection is never shared between requests.
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA cache_size = -{CACHE_SIZE_KIB}")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT version_num FROM alembic_version LIMIT 1"
    ).fetchone() if _table_exists(conn, "alembic_version") else None
    return row[0] if row else None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
