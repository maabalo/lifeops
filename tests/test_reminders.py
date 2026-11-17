"""M4: reminders follow tasks, the scheduler sends neutral pushes, subscriptions are safe, housekeeping."""

from __future__ import annotations

import sys
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import reminders
from app.db import connect

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
KEYS = {"p256dh": "B" + "A" * 86, "auth": "c" * 22}


def manila_today() -> date:
    return datetime.now(ZoneInfo("Asia/Manila")).date()


def rows(app, sql, *args):
    with closing(connect(app.state.settings)) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def test_a_due_task_gets_one_reminder_at_the_reminder_time(me, app):
    due = manila_today() + timedelta(days=3)
    task = me.post("/api/tasks", json={"title": "Pay water", "due_date": due.isoformat()}).json()
    pending = rows(app, "SELECT * FROM reminders WHERE state = 'pending'")
    assert len(pending) == 1 and pending[0]["task_id"] == task["id"]
    expected = datetime(due.year, due.month, due.day, 8, 0, tzinfo=ZoneInfo("Asia/Manila")).astimezone(timezone.utc)
    assert pending[0]["remind_at"] == expected.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_reminders_follow_edits_snooze_and_completion(me, app):
    due = manila_today() + timedelta(days=2)
    task = me.post("/api/tasks", json={"title": "Renew", "due_date": due.isoformat()}).json()
    edited = me.patch(f"/api/tasks/{task['id']}", json={"version": 1, "due_date": (due + timedelta(days=5)).isoformat()}).json()
    states = [r["state"] for r in rows(app, "SELECT state FROM reminders ORDER BY id")]
    assert states == ["cancelled", "pending"]
    snoozed = me.post(f"/api/tasks/{task['id']}/snooze", json={"version": edited["version"], "until": (due + timedelta(days=9)).isoformat()}).json()
    assert len(rows(app, "SELECT * FROM reminders WHERE state = 'pending'")) == 1
    me.post(f"/api/tasks/{task['id']}/status", json={"version": snoozed["version"], "action": "complete"})
    assert rows(app, "SELECT * FROM reminders WHERE state = 'pending'") == []


def test_no_reminder_for_past_or_undated_tasks(me, app):
    me.post("/api/tasks", json={"title": "Someday"})
    me.post("/api/tasks", json={"title": "Late", "due_date": (manila_today() - timedelta(days=1)).isoformat()})
    assert rows(app, "SELECT * FROM reminders") == []


def test_changing_the_reminder_time_replans(me, app):
    me.post("/api/tasks", json={"title": "x", "due_date": (manila_today() + timedelta(days=1)).isoformat()})
    me.patch("/api/me", json={"reminder_time": "19:30"})
    pending = rows(app, "SELECT remind_at FROM reminders WHERE state = 'pending'")
    assert len(pending) == 1
    local = datetime.fromisoformat(pending[0]["remind_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Manila"))
    assert (local.hour, local.minute) == (19, 30)
    assert me.patch("/api/me", json={"reminder_time": "25:00"}).status_code == 422


def test_scheduler_sends_one_neutral_push_per_person(me, app):
    me.post("/api/push/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS})
    tomorrow = (manila_today() + timedelta(days=1)).isoformat()
    me.post("/api/tasks", json={"title": "Pay Meralco 2,345.60", "due_date": tomorrow})
    me.post("/api/tasks", json={"title": "Secret thing", "due_date": tomorrow})
    later = datetime.now(timezone.utc) + timedelta(days=3)
    with closing(connect(app.state.settings)) as conn:
        assert reminders.tick(conn, app.state.push_sender(), now=later) == 1
        assert reminders.tick(conn, app.state.push_sender(), now=later) == 0  # never twice
    assert len(app.state.sent) == 1
    payload = app.state.sent[0][1]
    assert payload["body"] == "You have 2 things due today."
    assert "Meralco" not in str(payload) and "Secret" not in str(payload)


def test_gone_devices_are_removed(me, app):
    me.post("/api/push/subscribe", json={"endpoint": "https://fcm.googleapis.com/fcm/send/gone-device", "keys": KEYS})
    assert me.post("/api/push/test").status_code == 409  # 410 from the push service: nothing delivered
    assert rows(app, "SELECT * FROM push_subscriptions") == []


def test_only_real_push_services_are_accepted(me):
    for endpoint in ("http://fcm.googleapis.com/x", "https://127.0.0.1:8780/api", "https://evil.example/push", "https://fcm.googleapis.com.evil.example/x"):
        response = me.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": KEYS})
        assert response.status_code == 422, endpoint
    ok = me.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/abc", "keys": KEYS})
    assert ok.status_code == 201
    assert me.post("/api/push/test").json()["delivered"] == 1


def test_push_key_is_made_once_and_kept_private(me, app):
    first = me.get("/api/push/key").json()["public_key"]
    assert first == me.get("/api/push/key").json()["public_key"]
    key_file = app.state.settings.data_dir / "vapid_private.pem"
    assert key_file.is_file()
    if sys.platform != "win32":  # Windows has no Unix permission bits
        assert (key_file.stat().st_mode & 0o077) == 0


def test_housekeeping_removes_only_old_things(me, app):
    note = me.post("/api/inbox", json={"content": "Bill due 1 May 2027"}).json()
    item = me.post(f"/api/inbox/{note['id']}/suggest", json={"version": note["version"]}).json()
    me.post(f"/api/inbox/{note['id']}/reject", json={"version": item["version"]})
    with closing(connect(app.state.settings)) as conn:
        assert reminders.housekeeping(conn)["proposals"] == 0  # too new
        removed = reminders.housekeeping(conn, today=datetime.now(timezone.utc) + timedelta(days=100))
    assert removed["proposals"] == 1
    assert rows(app, "SELECT * FROM inbox_items")  # the note itself stays


def test_settings_show_reader_and_claude_needs_a_key(me):
    data = me.get("/api/me").json()
    assert data["extractor"] == "mock" and data["claude_available"] is False
    assert me.patch("/api/me", json={"extractor": "claude"}).status_code == 409


def test_backup_and_restore_round_trip(me, app, tmp_path):
    from app import backup
    from tests.test_documents import BILL, upload

    upload(me, "bill.pdf", BILL)
    me.post("/api/tasks", json={"title": "Keep me"})
    folder = backup.backup(app.state.settings, into=tmp_path / "backups")
    assert backup.check(folder) == 1
    assert (folder / "files").is_dir()
    if sys.platform != "win32":  # Windows has no Unix permission bits
        assert (folder / "lifeops.sqlite3").stat().st_mode & 0o077 == 0
    me.post("/api/account/delete", json={"scope": "data", "password": "correct horse battery staple", "confirm": "DELETE"})
    aside = backup.restore(app.state.settings, folder)
    assert aside.is_dir()  # the data before the restore was kept, not lost
    titles = [t["title"] for t in me.get("/api/tasks").json()["tasks"]]
    assert titles == ["Keep me"]
    assert len(list((app.state.settings.data_dir / "files").iterdir())) == 1
