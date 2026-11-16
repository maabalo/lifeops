"""Inbox and tasks: happy paths, validation, ownership (IDOR), duplicates, state changes, versions, Today."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def manila_today() -> date:
    return datetime.now(ZoneInfo("Asia/Manila")).date()


def test_capture_list_archive_restore_delete(me):
    item = me.post("/api/inbox", json={"content": "Aircon warranty ends 12 March"}).json()
    assert item["status"] == "new"
    assert [i["id"] for i in me.get("/api/inbox").json()["items"]] == [item["id"]]
    archived = me.post(f"/api/inbox/{item['id']}/archive", json={"version": item["version"]}).json()
    assert archived["status"] == "archived"
    assert me.get("/api/inbox").json()["items"] == []
    restored = me.post(f"/api/inbox/{item['id']}/restore", json={"version": archived["version"]}).json()
    assert restored["status"] == "new"
    assert me.delete(f"/api/inbox/{item['id']}").status_code == 204
    assert me.get("/api/inbox").json()["items"] == []


def test_validation_errors_are_422_and_do_not_echo_input(me):
    response = me.post("/api/inbox", json={"content": ""})
    assert response.status_code == 422
    secret = "my-account-number-1234"
    response = me.post("/api/tasks", json={"title": "x", "category": secret})
    assert response.status_code == 422
    assert secret not in response.text
    assert me.post("/api/tasks", json={"title": "x", "surprise": 1}).status_code == 422


def test_same_idempotency_key_creates_only_one_item(me):
    headers = {"Idempotency-Key": "abc12345-0000-4000-8000-000000000001"}
    first = me.post("/api/inbox", json={"content": "Pay water bill"}, headers=headers).json()
    again = me.post("/api/inbox", json={"content": "Pay water bill"}, headers=headers).json()
    assert first == again
    assert len(me.get("/api/inbox").json()["items"]) == 1
    bad = me.post("/api/inbox", json={"content": "x"}, headers={"Idempotency-Key": "no spaces allowed"})
    assert bad.status_code == 422


def test_note_becomes_a_task_once(me):
    item = me.post("/api/inbox", json={"content": "Renew car registration"}).json()
    due = (manila_today() + timedelta(days=3)).isoformat()
    task = me.post(
        f"/api/inbox/{item['id']}/task",
        json={"version": item["version"], "title": "Renew registration", "category": "vehicle", "due_date": due},
    ).json()
    assert task["source_inbox_id"] == item["id"] and task["due_date"] == due
    assert me.get("/api/inbox?show=converted").json()["items"][0]["status"] == "converted"
    again = me.post(f"/api/inbox/{item['id']}/task", json={"version": item["version"], "title": "x"})
    assert again.status_code == 409


def test_another_users_items_look_missing(me, other):
    item = me.post("/api/inbox", json={"content": "private"}).json()
    task = me.post("/api/tasks", json={"title": "private"}).json()
    assert other.get("/api/inbox").json()["items"] == []
    assert other.get("/api/tasks").json()["tasks"] == []
    assert other.post(f"/api/inbox/{item['id']}/archive", json={"version": 1}).status_code == 404
    assert other.delete(f"/api/inbox/{item['id']}").status_code == 404
    assert other.patch(f"/api/tasks/{task['id']}", json={"version": 1, "title": "hacked"}).status_code == 404
    assert other.post(f"/api/tasks/{task['id']}/status", json={"version": 1, "action": "complete"}).status_code == 404
    assert other.delete(f"/api/tasks/{task['id']}").status_code == 404
    assert me.get("/api/tasks").json()["tasks"][0]["title"] == "private"


def test_task_status_changes_follow_the_rules(me):
    task = me.post("/api/tasks", json={"title": "Pay electricity"}).json()
    done = me.post(f"/api/tasks/{task['id']}/status", json={"version": 1, "action": "complete"}).json()
    assert done["status"] == "done" and done["completed_at"]
    again = me.post(f"/api/tasks/{task['id']}/status", json={"version": done["version"], "action": "complete"})
    assert again.status_code == 409
    reopened = me.post(f"/api/tasks/{task['id']}/status", json={"version": done["version"], "action": "reopen"}).json()
    assert reopened["status"] == "open" and reopened["completed_at"] is None


def test_stale_version_is_refused(me):
    task = me.post("/api/tasks", json={"title": "Old"}).json()
    me.patch(f"/api/tasks/{task['id']}", json={"version": 1, "title": "New"})
    stale = me.patch(f"/api/tasks/{task['id']}", json={"version": 1, "title": "Older edit"})
    assert stale.status_code == 409
    assert me.get("/api/tasks").json()["tasks"][0]["title"] == "New"


def test_edit_and_clear_due_date(me):
    task = me.post("/api/tasks", json={"title": "Dentist", "due_date": "2027-01-05"}).json()
    edited = me.patch(f"/api/tasks/{task['id']}", json={"version": 1, "details": "Bring card"}).json()
    assert edited["due_date"] == "2027-01-05" and edited["details"] == "Bring card"
    cleared = me.patch(f"/api/tasks/{task['id']}", json={"version": 2, "clear_due_date": True}).json()
    assert cleared["due_date"] is None


def test_today_groups_by_the_users_calendar_date(me):
    today = manila_today()
    make = lambda title, days: me.post(  # noqa: E731
        "/api/tasks", json={"title": title, "due_date": (today + timedelta(days=days)).isoformat()}
    ).json()
    overdue = make("overdue", -1)
    due_today = make("today", 0)
    later = make("later", 20)
    no_date = me.post("/api/tasks", json={"title": "someday"}).json()
    snoozed = make("snoozed", -3)
    me.post(f"/api/tasks/{snoozed['id']}/snooze", json={"version": 1, "until": (today + timedelta(days=2)).isoformat()})
    finished = make("finished", 1)
    me.post(f"/api/tasks/{finished['id']}/status", json={"version": 1, "action": "complete"})
    view = me.get("/api/today").json()
    ids = lambda group: [t["id"] for t in view[group]]  # noqa: E731
    assert view["today"] == today.isoformat()
    assert ids("overdue") == [overdue["id"]]
    assert ids("due_soon") == [due_today["id"]]
    assert set(ids("later")) == {later["id"], no_date["id"]}
    assert ids("snoozed") == [snoozed["id"]]
    assert ids("done") == [finished["id"]]


def test_snooze_must_be_in_the_future_and_open(me):
    task = me.post("/api/tasks", json={"title": "x"}).json()
    today = manila_today().isoformat()
    assert me.post(f"/api/tasks/{task['id']}/snooze", json={"version": 1, "until": today}).status_code == 422


def test_activity_never_stores_content(me, app):
    from contextlib import closing

    from app.db import connect

    me.post("/api/inbox", json={"content": "Bank account 0000-1111-2222"})
    with closing(connect(app.state.settings)) as conn:
        rows = [dict(r) for r in conn.execute("SELECT entity, action, detail FROM audit_events")]
    assert any(r["action"] == "captured" for r in rows)
    assert "0000-1111-2222" not in str(rows)
