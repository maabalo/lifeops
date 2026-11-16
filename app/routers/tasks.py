"""Tasks: create, list, edit, complete / reopen / cancel, snooze, delete; and the Today view."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import reminders
from ..db import transaction, utc_now
from ..models import SnoozeIn, TaskIn, TaskPatch, TaskStatusIn
from ..security import CurrentUser, current_user, get_conn
from ..services import (
    TASK_TRANSITIONS,
    audit,
    check_version,
    idempotency_key,
    owned_row,
    remember,
    replay,
    task_out,
)

router = APIRouter(prefix="/api", tags=["tasks"])

DUE_SOON_DAYS = 7
DONE_SHOWN_DAYS = 30
BILLS_AHEAD_DAYS = 30  # dashboard: bills due in the next 30 days…
BILLS_BEHIND_DAYS = 30  # …and unpaid ones up to 30 days overdue
EXPIRING_DAYS = 90  # dashboard: warranties and renewals ending in the next 90 days
DASHBOARD_ROWS = 10


def today_for(user: CurrentUser) -> date:
    """The user's calendar date (due dates are dates, so 'today' depends on their timezone)."""
    return datetime.now(ZoneInfo(user.timezone)).date()


@router.get("/tasks")
def list_tasks(
    status: str = "open", user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    if status not in {"open", "done", "cancelled", "all"}:
        raise HTTPException(status_code=422, detail="status must be open, done, cancelled or all.")
    if status == "all":
        rows = conn.execute("SELECT * FROM tasks WHERE owner_id = ? ORDER BY due_date IS NULL, due_date, id", (user.id,))
    else:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE owner_id = ? AND status = ? ORDER BY due_date IS NULL, due_date, id",
            (user.id, status),
        )
    return {"tasks": [task_out(row) for row in rows.fetchall()]}


@router.post("/tasks", status_code=201)
def create_task(
    body: TaskIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    key = idempotency_key(request)
    with transaction(conn):
        earlier = replay(conn, user.id, key, "tasks.create")
        if earlier is not None:
            return earlier
        now = utc_now()
        cur = conn.execute(
            """INSERT INTO tasks (owner_id, title, details, category, due_date, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            (user.id, body.title, body.details, body.category, body.due_date and body.due_date.isoformat(), now, now),
        )
        reminders.sync_task(conn, user.id, cur.lastrowid)
        task = task_out(owned_row(conn, "tasks", user.id, cur.lastrowid))
        audit(conn, user.id, "task", task["id"], "created")
        remember(conn, user.id, key, "tasks.create", task)
    return task


@router.patch("/tasks/{task_id}")
def edit_task(task_id: int, body: TaskPatch, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    with transaction(conn):
        row = owned_row(conn, "tasks", user.id, task_id)
        check_version(row, body.version)
        fields = {
            "title": body.title if body.title is not None else row["title"],
            "details": body.details if body.details is not None else row["details"],
            "category": body.category or row["category"],
            "due_date": None if body.clear_due_date else (body.due_date.isoformat() if body.due_date else row["due_date"]),
        }
        conn.execute(
            """UPDATE tasks SET title = ?, details = ?, category = ?, due_date = ?, version = version + 1, updated_at = ?
               WHERE id = ? AND owner_id = ? AND version = ?""",
            (*fields.values(), utc_now(), task_id, user.id, body.version),
        )
        audit(conn, user.id, "task", task_id, "edited")
        reminders.sync_task(conn, user.id, task_id)
        return task_out(owned_row(conn, "tasks", user.id, task_id))


@router.post("/tasks/{task_id}/status")
def change_status(task_id: int, body: TaskStatusIn, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    allowed_from, to = TASK_TRANSITIONS[body.action]
    with transaction(conn):
        row = owned_row(conn, "tasks", user.id, task_id)
        check_version(row, body.version)
        if row["status"] not in allowed_from:
            raise HTTPException(status_code=409, detail=f"A {row['status']} task can't be marked that way.")
        now = utc_now()
        conn.execute(
            """UPDATE tasks SET status = ?, completed_at = ?, snoozed_until = NULL, version = version + 1, updated_at = ?
               WHERE id = ? AND owner_id = ?""",
            (to, now if to == "done" else None, now, task_id, user.id),
        )
        audit(conn, user.id, "task", task_id, body.action)
        reminders.sync_task(conn, user.id, task_id)
        return task_out(owned_row(conn, "tasks", user.id, task_id))


@router.post("/tasks/{task_id}/snooze")
def snooze(task_id: int, body: SnoozeIn, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    if body.until <= today_for(user):
        raise HTTPException(status_code=422, detail="Snooze until a day after today.")
    with transaction(conn):
        row = owned_row(conn, "tasks", user.id, task_id)
        check_version(row, body.version)
        if row["status"] != "open":
            raise HTTPException(status_code=409, detail="Only open tasks can be snoozed.")
        conn.execute(
            "UPDATE tasks SET snoozed_until = ?, version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
            (body.until.isoformat(), utc_now(), task_id, user.id),
        )
        audit(conn, user.id, "task", task_id, "snoozed")
        reminders.sync_task(conn, user.id, task_id)
        return task_out(owned_row(conn, "tasks", user.id, task_id))


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> None:
    with transaction(conn):
        owned_row(conn, "tasks", user.id, task_id)
        conn.execute("DELETE FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user.id))
        audit(conn, user.id, "task", task_id, "deleted")


@router.get("/today")
def today(user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Overdue, due soon (next 7 days), later / no date, and done in the last 30 days. Snoozed tasks stay
    out of overdue and due-soon until their snooze date."""
    day = today_for(user)
    soon = day + timedelta(days=DUE_SOON_DAYS)
    groups: dict[str, list] = {"overdue": [], "due_soon": [], "later": [], "snoozed": [], "done": []}
    for row in conn.execute("SELECT * FROM tasks WHERE owner_id = ? AND status = 'open'", (user.id,)).fetchall():
        task = task_out(row)
        snoozed = task["snoozed_until"] and date.fromisoformat(task["snoozed_until"]) > day
        due = date.fromisoformat(task["due_date"]) if task["due_date"] else None
        if snoozed:
            groups["snoozed"].append(task)
        elif due and due < day:
            groups["overdue"].append(task)
        elif due and due <= soon:
            groups["due_soon"].append(task)
        else:
            groups["later"].append(task)
    since = (datetime.now(ZoneInfo("UTC")) - timedelta(days=DONE_SHOWN_DAYS)).isoformat()
    done = conn.execute(
        "SELECT * FROM tasks WHERE owner_id = ? AND status = 'done' AND completed_at >= ? ORDER BY completed_at DESC",
        (user.id, since[:19] + "Z"),
    ).fetchall()
    groups["done"] = [task_out(row) for row in done]
    by_due = lambda t: (t["due_date"] is None, t["due_date"] or "", t["id"])  # noqa: E731
    for name in ("overdue", "due_soon", "later", "snoozed"):
        groups[name].sort(key=by_due)
    inbox = conn.execute(
        "SELECT COUNT(*) FROM inbox_items WHERE owner_id = ? AND status IN ('new', 'needs_review', 'failed')", (user.id,)
    ).fetchone()[0]
    return {
        "today": day.isoformat(),
        "timezone": user.timezone,
        "inbox_waiting": inbox,
        **groups,
        **dashboard(conn, user.id, day),
    }


def dashboard(conn: sqlite3.Connection, owner_id: int, day: date) -> dict:
    """Bills to pay soon (with their total per currency) and things expiring soon, from the user's Records.
    A bill counts as paid once its "pay" task (made from the record) is done; then it drops off."""
    bills = conn.execute(
        """SELECT id, title, supplier, amount_cents, currency, due_date FROM records r
           WHERE owner_id = ? AND doc_type = 'bill' AND due_date BETWEEN ? AND ?
             AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.source_record_id = r.id AND t.owner_id = r.owner_id
                             AND t.status = 'done')
           ORDER BY due_date, id""",
        (owner_id, (day - timedelta(days=BILLS_BEHIND_DAYS)).isoformat(), (day + timedelta(days=BILLS_AHEAD_DAYS)).isoformat()),
    ).fetchall()
    totals: dict[str, int] = {}
    for bill in bills:
        if bill["amount_cents"] is not None and bill["currency"]:
            totals[bill["currency"]] = totals.get(bill["currency"], 0) + bill["amount_cents"]
    expiring = conn.execute(
        """SELECT id, doc_type, title, supplier, expiry_date FROM records
           WHERE owner_id = ? AND expiry_date BETWEEN ? AND ? ORDER BY expiry_date, id LIMIT ?""",
        (owner_id, day.isoformat(), (day + timedelta(days=EXPIRING_DAYS)).isoformat(), DASHBOARD_ROWS),
    ).fetchall()
    money = lambda cents: f"{cents / 100:.2f}" if cents is not None else None  # noqa: E731
    return {
        "bills_due": [
            {"id": b["id"], "title": b["title"], "supplier": b["supplier"], "amount": money(b["amount_cents"]),
             "currency": b["currency"], "due_date": b["due_date"]}
            for b in bills[:DASHBOARD_ROWS]
        ],  # fmt: skip
        "bills_due_count": len(bills),
        "bills_total": [{"currency": c, "amount": money(cents)} for c, cents in sorted(totals.items())],
        "expiring": [dict(row) for row in expiring],
    }
