"""
LifeOps workflow runs: the reading/extraction of an Inbox item as an explicit, recorded state machine.

    queued → running → needs_review → completed      (the user accepted or rejected the suggestions)
                     ↘ failed → running (retry)       (attempts + 1)
    queued / failed / needs_review → cancelled        (the item was archived or deleted)

One function changes state (`move`): it checks the transition, records the step, bumps the version and
writes an activity event with the run's correlation id. Anything not in TRANSITIONS is refused (409).
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from fastapi import HTTPException

from .db import utc_now
from .services import audit

TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"needs_review", "failed", "completed"},
    "needs_review": {"completed", "cancelled"},
    "failed": {"running", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def start(conn: sqlite3.Connection, owner_id: int, item_id: int) -> sqlite3.Row:
    """A new run for an item (queued, then immediately running: work happens inside the request for now)."""
    now = utc_now()
    cur = conn.execute(
        """INSERT INTO workflow_runs (owner_id, inbox_item_id, kind, state, attempts, correlation_id, created_at, updated_at)
           VALUES (?, ?, 'extract', 'queued', 0, ?, ?, ?)""",
        (owner_id, item_id, uuid.uuid4().hex, now, now),
    )
    run = get(conn, owner_id, cur.lastrowid)
    return move(conn, run, "running", step="start")


def get(conn: sqlite3.Connection, owner_id: int, run_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM workflow_runs WHERE id = ? AND owner_id = ?", (run_id, owner_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return row


def latest(conn: sqlite3.Connection, owner_id: int, item_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM workflow_runs WHERE owner_id = ? AND inbox_item_id = ? ORDER BY id DESC LIMIT 1",
        (owner_id, item_id),
    ).fetchone()


def move(conn: sqlite3.Connection, run: sqlite3.Row, to: str, step: str, error: str = "") -> sqlite3.Row:
    """The only way a run changes state."""
    if to not in TRANSITIONS.get(run["state"], set()):
        raise HTTPException(status_code=409, detail=f"A {run['state']} run can't become {to}.")
    steps = json.loads(run["steps"])
    steps.append({"step": step, "state": to, "at": utc_now(), **({"error": error[:200]} if error else {})})
    attempts = run["attempts"] + (1 if to == "running" else 0)
    conn.execute(
        """UPDATE workflow_runs SET state = ?, attempts = ?, steps = ?, error = ?, version = version + 1, updated_at = ?
           WHERE id = ? AND owner_id = ? AND version = ?""",
        (to, attempts, json.dumps(steps), error[:300], utc_now(), run["id"], run["owner_id"], run["version"]),
    )
    audit(conn, run["owner_id"], "run", run["id"], to, f"{step} · {run['correlation_id'][:8]}")
    return get(conn, run["owner_id"], run["id"])


def finish_open(conn: sqlite3.Connection, owner_id: int, item_id: int, to: str, step: str) -> None:
    """Close the item's current run when the user decides (completed) or the item goes away (cancelled)."""
    run = latest(conn, owner_id, item_id)
    if run is not None and to in TRANSITIONS.get(run["state"], set()):
        move(conn, run, to, step=step)


def run_out(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "state": row["state"],
        "attempts": row["attempts"],
        "correlation_id": row["correlation_id"],
        "steps": json.loads(row["steps"]),
        "error": row["error"],
    }


def fail_interrupted(conn: sqlite3.Connection) -> int:
    """At server start: reads that were in progress when the server stopped can't finish any more. Mark them
    failed with a plain reason, so the item doesn't stay on "Reading…" and Try again works. Returns how many."""
    from .db import transaction, utc_now  # local import: db doesn't depend on workflow

    count = 0
    with transaction(conn):
        for run in conn.execute("SELECT * FROM workflow_runs WHERE state IN ('queued', 'running')").fetchall():
            if run["state"] == "queued":
                run = move(conn, run, "running", step="start")
            move(conn, run, "failed", step="extract", error="interrupted: the server stopped while reading")
            conn.execute(
                """UPDATE inbox_items SET status = 'failed', error = 'Reading was interrupted. Try again.',
                   version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ? AND status = 'processing'""",
                (utc_now(), run["inbox_item_id"], run["owner_id"]),
            )
            count += 1
    return count
