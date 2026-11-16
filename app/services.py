"""
LifeOps shared rules: the activity log, duplicate protection (idempotency keys), task status changes and
optimistic versions. Routers stay thin and call these.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from fastapi import HTTPException, Request

from .db import utc_now

IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,80}$")

# Allowed task status changes: action → (from statuses, to status). Anything else is refused (409).
TASK_TRANSITIONS = {
    "complete": ({"open"}, "done"),
    "reopen": ({"done", "cancelled"}, "open"),
    "cancel": ({"open"}, "cancelled"),
}


def audit(conn: sqlite3.Connection, owner_id: int, entity: str, entity_id: int | None, action: str, detail: str = "") -> None:
    """Record what happened (never the content: titles, notes and documents stay out of the log)."""
    conn.execute(
        "INSERT INTO audit_events (owner_id, at, entity, entity_id, action, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (owner_id, utc_now(), entity, entity_id, action, detail),
    )


# ---- duplicate protection ------------------------------------------------------------------------
def idempotency_key(request: Request) -> str | None:
    key = request.headers.get(IDEMPOTENCY_HEADER)
    if key is None:
        return None
    if not IDEMPOTENCY_PATTERN.match(key):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be 8-80 letters, digits or dashes.")
    return key


def replay(conn: sqlite3.Connection, owner_id: int, key: str | None, route: str) -> Any | None:
    """The stored answer for a repeated request, or None for a new one."""
    if key is None:
        return None
    row = conn.execute(
        "SELECT route, response FROM idempotency_keys WHERE owner_id = ? AND key = ?", (owner_id, key)
    ).fetchone()
    if row is None:
        return None
    if row["route"] != route:
        raise HTTPException(status_code=422, detail="That Idempotency-Key was already used for another action.")
    return json.loads(row["response"])


def remember(conn: sqlite3.Connection, owner_id: int, key: str | None, route: str, response: Any) -> None:
    if key is not None:
        conn.execute(
            "INSERT INTO idempotency_keys (owner_id, key, route, response, created_at) VALUES (?, ?, ?, ?, ?)",
            (owner_id, key, route, json.dumps(response), utc_now()),
        )


# ---- rows ---------------------------------------------------------------------------------------
def owned_row(conn: sqlite3.Connection, table: str, owner_id: int, row_id: int) -> sqlite3.Row:
    """A row that belongs to this owner. Missing and not-yours look the same (404)."""
    if table not in {"inbox_items", "tasks"}:
        raise ValueError(table)
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ? AND owner_id = ?", (row_id, owner_id)).fetchone()  # noqa: S608
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return row


def check_version(row: sqlite3.Row, version: int) -> None:
    """Optimistic concurrency: the change must be based on the latest version."""
    if row["version"] != version:
        raise HTTPException(status_code=409, detail="This item changed in another window. Reload to see the latest.")


def task_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "details": row["details"],
        "category": row["category"],
        "due_date": row["due_date"],
        "snoozed_until": row["snoozed_until"],
        "status": row["status"],
        "source_inbox_id": row["source_inbox_id"],
        "source_record_id": row["source_record_id"] if "source_record_id" in row.keys() else None,
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
    }


def inbox_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "content": row["content"],
        "status": row["status"],
        "error": row["error"] if "error" in row.keys() else "",
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
