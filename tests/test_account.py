"""M3: workflow runs, activity, export and delete."""

from __future__ import annotations

import csv
import io
import json
import zipfile

from .conftest import PASSWORD
from .test_documents import BILL, upload


def test_upload_run_goes_running_then_needs_review_then_completed(me):
    item = upload(me, "bill.pdf", BILL).json()
    run = me.get(f"/api/inbox/{item['id']}/review").json()["run"]
    assert run["state"] == "needs_review" and run["attempts"] == 1
    assert [s["state"] for s in run["steps"]] == ["running", "needs_review"]
    me.post(f"/api/inbox/{item['id']}/accept", json={"version": item["version"], "doc_type": "bill", "title": "Bill"})
    review = me.get(f"/api/inbox/{item['id']}/review").json()
    assert review["run"]["state"] == "completed"


def test_failed_run_is_retried_with_one_more_attempt(me, app):
    from contextlib import closing

    from app import workflow
    from app.db import connect

    item = upload(me, "bill.pdf", BILL).json()
    # Simulate a failure after the read (e.g. the provider timed out): mark the run and item failed
    with closing(connect(app.state.settings)) as conn:
        run = workflow.latest(conn, 1, item["id"])
        conn.execute("UPDATE workflow_runs SET state = 'failed' WHERE id = ?", (run["id"],))
        conn.execute("UPDATE inbox_items SET status = 'failed', error = 'Timed out' WHERE id = ?", (item["id"],))
        version = conn.execute("SELECT version FROM inbox_items WHERE id = ?", (item["id"],)).fetchone()[0]
    retried = me.post(f"/api/inbox/{item['id']}/retry", json={"version": version}).json()
    assert retried["status"] == "needs_review"
    run = me.get(f"/api/inbox/{item['id']}/review").json()["run"]
    assert run["attempts"] == 2 and run["steps"][-2]["step"] == "retry"


def test_illegal_run_changes_are_refused(app):
    from contextlib import closing

    import pytest
    from fastapi import HTTPException

    from app import workflow
    from app.db import connect

    with closing(connect(app.state.settings)) as conn:
        conn.execute("INSERT INTO users (id, username, password_hash, timezone, created_at) VALUES (9, 'x@y.z', 'x', 'UTC', 'now')")
        conn.execute("INSERT INTO inbox_items (id, owner_id, kind, content, status, created_at, updated_at) VALUES (9, 9, 'text', 'n', 'new', 'now', 'now')")
        run = workflow.start(conn, 9, 9)
        run = workflow.move(conn, run, "needs_review", step="extract")
        run = workflow.move(conn, run, "completed", step="accepted")
        with pytest.raises(HTTPException) as caught:
            workflow.move(conn, run, "running", step="again")
        assert caught.value.status_code == 409


def test_archiving_cancels_the_open_run(me):
    item = upload(me, "bill.pdf", BILL).json()
    me.post(f"/api/inbox/{item['id']}/archive", json={"version": item["version"]})
    assert me.get(f"/api/inbox/{item['id']}/review").json()["run"]["state"] == "cancelled"


def test_activity_is_newest_first_and_private(me, other):
    me.post("/api/inbox", json={"content": "Secret note 4444"})
    me.post("/api/tasks", json={"title": "Secret task"})
    events = me.get("/api/activity").json()["events"]
    assert events[0]["entity"] == "task" and events[0]["action"] == "created"
    assert "4444" not in json.dumps(events) and "Secret" not in json.dumps(events)
    assert all(e["entity"] == "session" for e in other.get("/api/activity").json()["events"])


def test_export_has_everything_and_nothing_of_anyone_else(me, other):
    upload(me, "bill.pdf", BILL)
    me.post("/api/tasks", json={"title": "=HYPERLINK(\"http://evil\")"})
    other.post("/api/tasks", json={"title": "not mine"})
    response = me.get("/api/export")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith('attachment; filename="lifeops-export-')
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert {"data.json", "tasks.csv", "records.csv", "README.txt"} <= set(names)
    assert any(n.startswith("files/") and n.endswith("bill.pdf") for n in names)
    data = json.loads(archive.read("data.json"))
    assert len(data["inbox_items"]) == 1 and len(data["files"]) == 1
    assert "not mine" not in archive.read("data.json").decode()
    rows = list(csv.DictReader(io.StringIO(archive.read("tasks.csv").decode())))
    assert rows[0]["title"].startswith("'=")  # spreadsheet formulas are neutralised


def test_delete_needs_password_and_the_word(me):
    assert me.post("/api/account/delete", json={"scope": "data", "password": PASSWORD, "confirm": "delete"}).status_code == 422
    assert me.post("/api/account/delete", json={"scope": "data", "password": "wrong", "confirm": "DELETE"}).status_code == 403


def test_delete_my_data_keeps_the_login_and_removes_files(me, other, app):
    upload(me, "bill.pdf", BILL)
    me.post("/api/tasks", json={"title": "mine"})
    other.post("/api/tasks", json={"title": "theirs"})
    folder = app.state.settings.data_dir / "files"
    assert len(list(folder.iterdir())) == 1
    result = me.post("/api/account/delete", json={"scope": "data", "password": PASSWORD, "confirm": "DELETE"}).json()
    assert result["files_removed"] == 1
    assert list(folder.iterdir()) == []
    assert me.get("/api/tasks?status=all").json()["tasks"] == []
    assert me.get("/api/inbox").json()["items"] == []
    assert me.get("/api/me").status_code == 200  # still logged in
    assert other.get("/api/tasks").json()["tasks"][0]["title"] == "theirs"


def test_delete_my_account_signs_out_everywhere(me):
    response = me.post("/api/account/delete", json={"scope": "account", "password": PASSWORD, "confirm": "DELETE"})
    assert response.status_code == 200
    assert me.get("/api/me").status_code == 401
    again = me.client.post("/api/login", json={"username": "me", "password": PASSWORD})
    assert again.status_code == 401
