"""
Your data (M3): activity history, export everything, delete everything.

    GET  /api/activity?before=ID          newest first, 50 at a time (what happened, never the content)
    GET  /api/export                      a ZIP: data.json, tasks.csv, records.csv and the original files
    POST /api/account/delete              {"scope": "data" | "account", "password": "...", "confirm": "DELETE"}

Delete needs your password and the word DELETE typed out. "data" removes everything you stored and keeps the
login; "account" removes the login too and signs you out. Files are removed from disk as well.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
import zipfile
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field

from ..db import transaction, utc_now
from ..documents import remove_stored
from ..models import Strict
from ..security import (
    CurrentUser,
    clear_session_cookie,
    current_user,
    get_conn,
    login_limiter,
    verify_password,
)
from ..services import audit

router = APIRouter(prefix="/api", tags=["account"])

ACTIVITY_PAGE = 50
# Tables you own, in an order that is safe to delete in (children first).
OWNED_TABLES = (
    "idempotency_keys", "push_subscriptions", "reminders", "workflow_runs", "tasks", "records", "proposals", "files",
    "inbox_items", "audit_events",
)  # fmt: skip
EXPORT_TABLES = ("inbox_items", "files", "proposals", "records", "tasks", "reminders", "workflow_runs", "audit_events")
# push_subscriptions are left out of the export on purpose: they are device keys, not your data


class DeleteIn(Strict):
    scope: Literal["data", "account"]
    password: str = Field(min_length=1, max_length=200)
    confirm: str = Field(max_length=20)


@router.get("/activity")
def activity(
    before: int | None = None, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    if before is None:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE owner_id = ? ORDER BY id DESC LIMIT ?", (user.id, ACTIVITY_PAGE)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE owner_id = ? AND id < ? ORDER BY id DESC LIMIT ?",
            (user.id, before, ACTIVITY_PAGE),
        ).fetchall()
    events = [
        {"id": r["id"], "at": r["at"], "entity": r["entity"], "entity_id": r["entity_id"], "action": r["action"], "detail": r["detail"]}
        for r in rows
    ]
    return {"events": events, "more": len(events) == ACTIVITY_PAGE}


def owned_rows(conn: sqlite3.Connection, table: str, owner_id: int) -> list[dict]:
    if table not in EXPORT_TABLES:
        raise ValueError(table)
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE owner_id = ? ORDER BY id", (owner_id,))]  # noqa: S608


def to_csv(rows: list[dict], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # A leading = + - @ would make spreadsheet apps run the cell as a formula: neutralise it
        writer.writerow({k: (f"'{v}" if isinstance(v, str) and v[:1] in "=+-@" else v) for k, v in row.items()})
    return buffer.getvalue()


@router.get("/export")
def export(request: Request, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> StreamingResponse:
    """Everything you own, made on request and streamed (nothing is kept on the server afterwards)."""
    data = {table: owned_rows(conn, table, user.id) for table in EXPORT_TABLES}
    for table in ("proposals", "workflow_runs"):
        for row in data[table]:
            for key in ("fields", "steps"):
                if key in row:
                    row[key] = json.loads(row[key])
    folder = request.app.state.settings.data_dir / "files"
    spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)  # spills to a temp file only when large
    with zipfile.ZipFile(spool, "w", zipfile.ZIP_DEFLATED) as archive:
        meta = {"exported_at": utc_now(), "account": user.username, "timezone": user.timezone, "format": "lifeops-export-1"}
        archive.writestr("data.json", json.dumps({"export": meta, **data}, indent=2, ensure_ascii=False))
        archive.writestr("tasks.csv", to_csv(data["tasks"], ["id", "title", "category", "due_date", "status", "details", "created_at", "completed_at"]))
        archive.writestr(
            "records.csv",
            to_csv(data["records"], ["id", "doc_type", "title", "supplier", "amount_cents", "currency", "reference", "due_date", "expiry_date", "created_at"]),
        )
        for f in data["files"]:
            path = folder / f["stored_name"]
            if path.is_file():
                safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in f["original_name"])[:100]
                archive.write(path, f"files/{f['id']}-{safe or 'document'}")
        archive.writestr(
            "README.txt",
            "LifeOps export\n\ndata.json: everything you stored (inbox, files, suggestions, records, tasks, runs, activity).\n"
            "tasks.csv, records.csv: the same tasks and records as spreadsheets.\nfiles/: your original uploads.\n",
        )
    audit(conn, user.id, "account", None, "exported")
    spool.seek(0)
    stamp = utc_now()[:10]

    def chunks():
        try:
            while block := spool.read(64 * 1024):
                yield block
        finally:
            spool.close()

    return StreamingResponse(
        chunks(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="lifeops-export-{stamp}.zip"', "Cache-Control": "no-store"},
    )


@router.post("/account/delete")
def delete_everything(
    body: DeleteIn,
    request: Request,
    response: Response,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Remove everything you stored (scope "data"), or that and your login (scope "account")."""
    if body.confirm != "DELETE":
        raise HTTPException(status_code=422, detail='Type DELETE (in capitals) to confirm.')
    address = f"delete:{request.client.host if request.client else 'unknown'}"
    if login_limiter.blocked(address):
        raise HTTPException(status_code=429, detail="Too many tries. Wait 15 minutes, then try again.")
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
    if not verify_password(row["password_hash"], body.password):
        login_limiter.failed(address)
        raise HTTPException(status_code=403, detail="That password isn't right.")
    folder = request.app.state.settings.data_dir / "files"
    stored = [r["stored_name"] for r in conn.execute("SELECT stored_name FROM files WHERE owner_id = ?", (user.id,))]
    with transaction(conn):
        if body.scope == "account":
            conn.execute("DELETE FROM users WHERE id = ?", (user.id,))  # cascades to every table and every session
        else:
            for table in OWNED_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE owner_id = ?", (user.id,))  # noqa: S608
            audit(conn, user.id, "account", None, "data_deleted")
    for name in stored:  # after the database commit: a crash here leaves only unreachable files, never broken rows
        try:
            remove_stored(folder, name)
        except OSError:
            continue
    if body.scope == "account":
        clear_session_cookie(response, request.app.state.settings.cookie_secure)
    return {"ok": True, "scope": body.scope, "files_removed": len(stored)}
