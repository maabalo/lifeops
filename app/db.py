"""
LifeOps database: SQLite with WAL mode and foreign keys, and numbered SQL migrations.

Every query in the app uses parameters (?), never string formatting with user input, and every query on
owned data includes `owner_id = ?` (see the routers).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .settings import ROOT, Settings

MIGRATIONS = ROOT / "migrations"


def utc_now() -> str:
    """An instant as ISO 8601 UTC with a Z, e.g. 2026-11-16T09:30:00Z."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect(settings: Settings) -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit, transactions are explicit (see transaction()).
    # check_same_thread=False: the connection is opened in the request middleware but used by the route on
    # FastAPI's worker thread. Safe: each request has its own connection and uses it one step at a time.
    conn = sqlite3.connect(settings.db_path, timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """All writes inside happen together, or not at all."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection, folder: Path = MIGRATIONS) -> list[str]:
    """Apply migrations/NNN_name.sql files that haven't run yet, in order. Returns the names applied."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    done = {row["name"] for row in conn.execute("SELECT name FROM schema_migrations")}
    pending = [p for p in sorted(folder.glob("[0-9][0-9][0-9]_*.sql")) if p.name not in done]
    if not pending:
        return []
    # SQLite's safe way to rebuild a table: foreign keys off (outside any transaction), migrate, then check
    # that every reference still points somewhere before switching them back on.
    conn.execute("PRAGMA foreign_keys = OFF")
    applied = []
    try:
        for path in pending:
            with transaction(conn):
                for statement in split_sql(path.read_text()):
                    conn.execute(statement)
                broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"{path.name} left {len(broken)} broken references")
                conn.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)", (path.name, utc_now()))
            applied.append(path.name)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return applied


def split_sql(script: str) -> list[str]:
    """Split a migration into statements (our migrations keep one statement per ;-terminated block)."""
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]
