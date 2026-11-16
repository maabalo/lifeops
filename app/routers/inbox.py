"""Inbox: capture a note, list, archive, restore, delete, and turn a note into a task (M1: text notes)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import transaction, utc_now
from ..models import InboxAction, InboxIn, TaskFromInbox
from ..security import CurrentUser, current_user, get_conn
from ..services import audit, check_version, idempotency_key, inbox_out, owned_row, remember, replay, task_out
from .. import reminders, workflow
from . import documents

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

ACTIVE = ("new", "processing", "needs_review", "failed")


@router.get("")
def list_inbox(
    show: str = "active", user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    statuses = {"active": ACTIVE, "archived": ("archived",), "converted": ("converted",)}.get(show)
    if statuses is None:
        raise HTTPException(status_code=422, detail="show must be active, archived or converted.")
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(
        f"SELECT * FROM inbox_items WHERE owner_id = ? AND status IN ({marks}) ORDER BY created_at DESC, id DESC",  # noqa: S608
        (user.id, *statuses),
    ).fetchall()
    return {"items": [documents.item_payload(conn, user.id, row["id"]) for row in rows]}


@router.post("", status_code=201)
def capture(
    body: InboxIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    key = idempotency_key(request)
    with transaction(conn):
        earlier = replay(conn, user.id, key, "inbox.capture")
        if earlier is not None:
            return earlier
        now = utc_now()
        cur = conn.execute(
            "INSERT INTO inbox_items (owner_id, kind, content, status, created_at, updated_at) VALUES (?, 'text', ?, 'new', ?, ?)",
            (user.id, body.content, now, now),
        )
        item = inbox_out(owned_row(conn, "inbox_items", user.id, cur.lastrowid))
        audit(conn, user.id, "inbox", item["id"], "captured", "text")
        remember(conn, user.id, key, "inbox.capture", item)
    return item


def set_status(conn: sqlite3.Connection, user: CurrentUser, item_id: int, version: int, allowed: set, status: str) -> dict:
    with transaction(conn):
        row = owned_row(conn, "inbox_items", user.id, item_id)
        check_version(row, version)
        if row["status"] not in allowed:
            raise HTTPException(status_code=409, detail=f"Can't do that while the item is {row['status']}.")
        conn.execute(
            "UPDATE inbox_items SET status = ?, version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
            (status, utc_now(), item_id, user.id),
        )
        audit(conn, user.id, "inbox", item_id, status)
        if status == "archived":
            workflow.finish_open(conn, user.id, item_id, "cancelled", step="archived")
        return inbox_out(owned_row(conn, "inbox_items", user.id, item_id))


@router.post("/{item_id}/archive")
def archive(item_id: int, body: InboxAction, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    return set_status(conn, user, item_id, body.version, set(ACTIVE), "archived")


@router.post("/{item_id}/restore")
def restore(item_id: int, body: InboxAction, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    return set_status(conn, user, item_id, body.version, {"archived"}, "new")


@router.delete("/{item_id}", status_code=204)
def delete(
    item_id: int, request: Request, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> None:
    with transaction(conn):
        owned_row(conn, "inbox_items", user.id, item_id)
        documents.delete_item_files(conn, request, user.id, item_id)
        conn.execute("DELETE FROM inbox_items WHERE id = ? AND owner_id = ?", (item_id, user.id))
        audit(conn, user.id, "inbox", item_id, "deleted")


@router.post("/{item_id}/task", status_code=201)
def make_task(
    item_id: int,
    body: TaskFromInbox,
    request: Request,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Turn a note into a task (task + note status + activity happen together, or not at all)."""
    key = idempotency_key(request)
    with transaction(conn):
        earlier = replay(conn, user.id, key, "inbox.task")
        if earlier is not None:
            return earlier
        row = owned_row(conn, "inbox_items", user.id, item_id)
        check_version(row, body.version)
        if row["status"] not in ACTIVE:
            raise HTTPException(status_code=409, detail=f"Can't make a task from an item that is {row['status']}.")
        now = utc_now()
        cur = conn.execute(
            """INSERT INTO tasks (owner_id, title, details, category, due_date, status, source_inbox_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (user.id, body.title, row["content"], body.category, body.due_date and body.due_date.isoformat(), item_id, now, now),
        )
        conn.execute(
            "UPDATE inbox_items SET status = 'converted', version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
            (now, item_id, user.id),
        )
        task = task_out(owned_row(conn, "tasks", user.id, cur.lastrowid))
        audit(conn, user.id, "inbox", item_id, "converted")
        audit(conn, user.id, "task", task["id"], "created", "from_inbox")
        reminders.sync_task(conn, user.id, task["id"])
        remember(conn, user.id, key, "inbox.task", task)
    return task
