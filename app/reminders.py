"""
LifeOps reminders and housekeeping (M4).

- Every open task with a due date gets ONE reminder, on its due day (or the day its snooze ends) at the user's
  reminder time (default 08:00) in their timezone. Editing, snoozing, completing or cancelling the task
  updates or cancels it; the dedup_key means the same reminder is never made or sent twice.
- A small background scheduler (one thread) checks every minute: due reminders become ONE neutral push per
  person ("You have 2 things due today"), then are marked sent. The in-app Today view always shows the same.
- Housekeeping, once a day: rejected/replaced suggestions after 90 days, duplicate-protection keys after
  30 days, sent/cancelled reminders after 90 days, activity after a year, expired logins.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .db import connect, transaction, utc_now
from .push import Sender, send_to_owner
from .services import audit
from .settings import Settings

log = logging.getLogger("lifeops.scheduler")

RETENTION_DAYS = {
    "proposals": 90,  # rejected or replaced suggestions
    "idempotency_keys": 30,
    "reminders": 90,  # sent or cancelled
    "audit_events": 365,
}


def to_utc_text(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def remind_moment(day: date, reminder_time: str, tz: str) -> datetime:
    hour, minute = (int(x) for x in reminder_time.split(":"))
    return datetime.combine(day, time(hour, minute), tzinfo=ZoneInfo(tz))


def sync_task(conn: sqlite3.Connection, owner_id: int, task_id: int) -> None:
    """Make the task's reminder match the task now (call after any change to a task)."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, owner_id)).fetchone()
    user = conn.execute("SELECT timezone, reminder_time FROM users WHERE id = ?", (owner_id,)).fetchone()
    wanted = None
    if task is not None and task["status"] == "open" and task["due_date"]:
        day = date.fromisoformat(task["due_date"])
        if task["snoozed_until"] and date.fromisoformat(task["snoozed_until"]) > day:
            day = date.fromisoformat(task["snoozed_until"])
        today = datetime.now(ZoneInfo(user["timezone"])).date()
        if day >= today:  # no reminders for days already gone (the Today view shows overdue tasks)
            wanted = (day, f"task:{task_id}:{day.isoformat()}:{user['reminder_time']}:{user['timezone']}")
    pending = conn.execute(
        "SELECT id, dedup_key FROM reminders WHERE owner_id = ? AND task_id = ? AND state = 'pending'", (owner_id, task_id)
    ).fetchall()
    for row in pending:
        if wanted is None or row["dedup_key"] != wanted[1]:
            conn.execute("UPDATE reminders SET state = 'cancelled' WHERE id = ?", (row["id"],))
    if wanted is None:
        return
    moment = remind_moment(wanted[0], user["reminder_time"], user["timezone"])
    conn.execute(
        """INSERT INTO reminders (owner_id, task_id, remind_at, timezone, state, dedup_key, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?) ON CONFLICT (owner_id, dedup_key) DO NOTHING""",
        (owner_id, task_id, to_utc_text(moment), user["timezone"], wanted[1], utc_now()),
    )


def sync_all(conn: sqlite3.Connection, owner_id: int) -> None:
    """After the reminder time or timezone changes: rebuild every task's reminder."""
    for row in conn.execute("SELECT id FROM tasks WHERE owner_id = ?", (owner_id,)).fetchall():
        sync_task(conn, owner_id, row["id"])


def due_message(count: int) -> dict:
    """Neutral lock-screen text: no titles, amounts or names."""
    things = "1 thing" if count == 1 else f"{count} things"
    return {"title": "LifeOps", "body": f"You have {things} due today.", "url": "/#/today", "tag": "lifeops-due"}


def tick(conn: sqlite3.Connection, sender: Sender, now: datetime | None = None) -> int:
    """Send what is due. Returns the number of people notified."""
    now_text = to_utc_text(now or datetime.now(timezone.utc))
    owners = [
        r["owner_id"]
        for r in conn.execute(
            "SELECT DISTINCT owner_id FROM reminders WHERE state = 'pending' AND remind_at <= ?", (now_text,)
        ).fetchall()
    ]
    notified = 0
    for owner_id in owners:
        with transaction(conn):
            due = conn.execute(
                "SELECT id FROM reminders WHERE owner_id = ? AND state = 'pending' AND remind_at <= ?", (owner_id, now_text)
            ).fetchall()
            if not due:
                continue
            ids = [r["id"] for r in due]
            conn.execute(
                f"UPDATE reminders SET state = 'sent', sent_at = ? WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608
                (utc_now(), *ids),
            )
            delivered = send_to_owner(conn, owner_id, due_message(len(ids)), sender)
            audit(conn, owner_id, "reminder", None, "sent", f"{len(ids)} due · {delivered} device(s)")
            notified += 1
    return notified


def housekeeping(conn: sqlite3.Connection, today: datetime | None = None) -> dict:
    """Delete what the retention rules say is no longer needed. Returns how many rows each rule removed."""
    now = today or datetime.now(timezone.utc)
    cutoff = {name: to_utc_text(now - timedelta(days=days)) for name, days in RETENTION_DAYS.items()}
    removed = {}
    with transaction(conn):
        removed["proposals"] = conn.execute(
            "DELETE FROM proposals WHERE status IN ('rejected', 'superseded') AND decided_at < ?", (cutoff["proposals"],)
        ).rowcount
        removed["idempotency_keys"] = conn.execute(
            "DELETE FROM idempotency_keys WHERE created_at < ?", (cutoff["idempotency_keys"],)
        ).rowcount
        removed["reminders"] = conn.execute(
            "DELETE FROM reminders WHERE state IN ('sent', 'cancelled') AND created_at < ?", (cutoff["reminders"],)
        ).rowcount
        removed["audit_events"] = conn.execute("DELETE FROM audit_events WHERE at < ?", (cutoff["audit_events"],)).rowcount
        removed["sessions"] = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (utc_now(),)).rowcount
    return removed


class Scheduler:
    """One background thread: reminders every `every` seconds, housekeeping once a day. Never crashes the app."""

    def __init__(self, settings: Settings, sender: Sender, every: int = 60) -> None:
        self.settings = settings
        self.sender = sender
        self.every = every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="lifeops-scheduler", daemon=True)
        self._last_housekeeping: date | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with closing(connect(self.settings)) as conn:
                    tick(conn, self.sender)
                    today = datetime.now(timezone.utc).date()
                    if self._last_housekeeping != today:
                        housekeeping(conn)
                        self._last_housekeeping = today
            except Exception:  # log (no personal data) and keep going
                log.exception("scheduler run failed")
            self._stop.wait(self.every)
