"""
Documents (M2): upload → read → extractor proposal → review → accept (record + optional task) or reject.

    POST /api/inbox/upload            multipart "file"           → inbox item (needs_review or failed)
    POST /api/inbox/{id}/suggest      run the extractor on a note → needs_review
    POST /api/inbox/{id}/retry        try reading/extracting again after a failure
    GET  /api/inbox/{id}/review       the item, its file and the pending proposal
    POST /api/inbox/{id}/accept       confirmed values → record (+ task), all together or nothing
    POST /api/inbox/{id}/reject       proposal kept for history; the note goes back to "new"
    GET  /api/records                 confirmed records
    GET  /api/files/{id}              download (owner only; photos may be shown inline for the review)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pydantic import Field

from ..db import transaction, utc_now
from ..claude_reader import ClaudeExtractor, ReaderError, Source
from ..local_reader import LocalExtractor, local_model
from ..documents import MAX_FILE_BYTES, DocumentError, check_and_store, for_claude, read_pdf, remove_stored
from ..extractor import DOC_TYPES, claude_available, get_extractor, validate_proposal
from ..models import Category, Strict
from ..security import CurrentUser, current_user, get_conn
from .. import reminders, workflow
from ..services import audit, check_version, idempotency_key, inbox_out, owned_row, remember, replay, task_out

router = APIRouter(prefix="/api", tags=["documents"])


class AcceptIn(Strict):
    version: int = Field(ge=1)
    doc_type: Literal["bill", "warranty", "renewal", "receipt", "other"]
    title: str = Field(min_length=1, max_length=200)
    supplier: str = Field(default="", max_length=200)
    amount: str | None = Field(default=None, max_length=20)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    reference: str = Field(default="", max_length=100)
    due_date: date | None = None
    expiry_date: date | None = None
    make_task: bool = False
    task_title: str | None = Field(default=None, max_length=200)
    task_due_date: date | None = None
    task_category: Category = "other"


class RejectIn(Strict):
    version: int = Field(ge=1)


def files_folder(request: Request):
    return request.app.state.settings.data_dir / "files"


def file_row(conn: sqlite3.Connection, owner_id: int, item_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM files WHERE owner_id = ? AND inbox_item_id = ?", (owner_id, item_id)
    ).fetchone()


def file_out(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["original_name"],
        "media_type": row["media_type"],
        "size": row["size"],
        "pages": row["pages"],
    }


def make_claude():
    """The Claude reader (tests replace this function with a fake)."""
    return ClaudeExtractor()


def make_local():
    """The local (Ollama) reader, or None when Ollama isn't running with a model (tests replace this)."""
    model = local_model()
    return LocalExtractor(model) if model else None


def reader_for(conn: sqlite3.Connection, owner_id: int):
    """The user's chosen reader. Claude only with an API key; the local model only while Ollama runs with a model
    (if it's closed, a clear failure the user can retry, rather than quietly using another reader)."""
    row = conn.execute("SELECT extractor FROM users WHERE id = ?", (owner_id,)).fetchone()
    choice = row["extractor"] if row is not None else "mock"
    if choice == "claude" and claude_available():
        return make_claude()
    if choice == "local":
        local = make_local()
        if local is None:
            raise ReaderError("Ollama isn't running (or has no model). Open the Ollama app, then Try again.")
        return local
    return get_extractor("mock")


@dataclass
class ReadResult:
    """What reading produced: a checked proposal, or a plain-words reason it failed."""

    name: str = ""
    proposal: dict | None = None
    note: str = ""
    error: str = ""  # shown to the user
    detail: str = ""  # kept on the workflow run
    step: str = "extract"


def read_document(conn: sqlite3.Connection, owner_id: int, source: Source) -> ReadResult:
    """Pick the user's reader and read the document. Runs OUTSIDE any transaction (a local model can take
    minutes; an open write would block every other change meanwhile) and never raises."""
    try:
        extractor = reader_for(conn, owner_id)
        if isinstance(extractor, (ClaudeExtractor, LocalExtractor)):  # these read the file itself, not just its text
            proposal = validate_proposal(extractor.extract(for_claude(source)))
        else:
            proposal = validate_proposal(extractor.extract(source.pages))
    except (ReaderError, DocumentError) as error:
        return ReadResult(error=str(error), detail=str(error))
    except (ValueError, TypeError) as error:
        return ReadResult(error="The suggestions couldn't be made. Try again.", detail=str(error))
    except Exception:  # a bug must not leave the item stuck on "reading…": record it and let the user retry
        logging.getLogger("lifeops").exception("Reading a document failed")
        return ReadResult(error="Something went wrong while reading. Try again.", detail="unexpected error")
    read_something = isinstance(extractor, (ClaudeExtractor, LocalExtractor)) or any(p.strip() for p in source.pages)
    note = "" if read_something else "No text could be read: fill in what you see (or switch the reader to Claude in Settings)."
    return ReadResult(name=extractor.name, proposal=proposal, note=note)


def begin_reading(conn: sqlite3.Connection, owner_id: int, item_id: int, run=None) -> sqlite3.Row:
    """Inside a transaction: the item shows as being read, with a running workflow run (a new one if needed)."""
    conn.execute(
        "UPDATE inbox_items SET status = 'processing', error = '', version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
        (utc_now(), item_id, owner_id),
    )
    return run if run is not None else workflow.start(conn, owner_id, item_id)


def save_result(conn: sqlite3.Connection, owner_id: int, item_id: int, run: sqlite3.Row, result: ReadResult) -> None:
    """Inside a transaction: a new pending proposal and the item ready for review, or the failure with its reason.
    Skipped if the run was stopped while reading (e.g. the item was archived meanwhile)."""
    run = workflow.get(conn, owner_id, run["id"])
    if run["state"] != "running":
        return
    if result.proposal is None:
        workflow.move(conn, run, "failed", step=result.step, error=result.detail)
        fail(conn, owner_id, item_id, result.error)
        return
    now = utc_now()
    conn.execute(
        "UPDATE proposals SET status = 'superseded', decided_at = ? WHERE owner_id = ? AND inbox_item_id = ? AND status = 'pending'",
        (now, owner_id, item_id),
    )
    conn.execute(
        """INSERT INTO proposals (owner_id, inbox_item_id, schema_version, extractor, status, fields, note, created_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (owner_id, item_id, result.proposal["schema"], result.name, json.dumps(result.proposal["fields"]), result.note, now),
    )
    conn.execute(
        "UPDATE inbox_items SET status = 'needs_review', error = '', version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
        (now, item_id, owner_id),
    )
    audit(conn, owner_id, "inbox", item_id, "proposal_made", result.name)
    workflow.move(conn, run, "needs_review", step="extract")


def fail(conn: sqlite3.Connection, owner_id: int, item_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE inbox_items SET status = 'failed', error = ?, version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
        (reason[:300], utc_now(), item_id, owner_id),
    )
    audit(conn, owner_id, "inbox", item_id, "failed")


@router.post("/inbox/upload", status_code=201)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    key = idempotency_key(request)
    earlier = replay(conn, user.id, key, "inbox.upload")
    if earlier is not None:
        return earlier
    data = await file.read(MAX_FILE_BYTES + 1)
    folder = files_folder(request)
    try:
        stored = check_and_store(data, file.filename, folder)
    except DocumentError as error:
        raise HTTPException(status_code=error.status, detail=str(error)) from None
    try:
        with transaction(conn):
            duplicate = conn.execute(
                "SELECT inbox_item_id FROM files WHERE owner_id = ? AND sha256 = ?", (user.id, stored.sha256)
            ).fetchone()
            if duplicate:
                raise HTTPException(status_code=409, detail="You've already uploaded this exact file.")
            now = utc_now()
            cur = conn.execute(
                "INSERT INTO inbox_items (owner_id, kind, content, status, created_at, updated_at) VALUES (?, 'file', ?, 'processing', ?, ?)",
                (user.id, stored.original_name, now, now),
            )
            item_id = cur.lastrowid
            conn.execute(
                """INSERT INTO files (owner_id, inbox_item_id, stored_name, original_name, media_type, size, sha256, pages, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (user.id, item_id, stored.stored_name, stored.original_name, stored.media_type, stored.size,
                 stored.sha256, stored.pages, now),
            )  # fmt: skip
            audit(conn, user.id, "inbox", item_id, "uploaded", stored.media_type)
            run = workflow.start(conn, user.id, item_id)
    except BaseException:
        remove_stored(folder, stored.stored_name)  # nothing saved: don't keep an orphan file
        raise
    # Read outside the transaction, on a worker thread: this route is async, so a slow reader would otherwise
    # freeze the whole server, and an open write would block every other change.
    try:
        source = Source(stored.extracted.pages, (folder / stored.stored_name).read_bytes(), stored.media_type)
        result = await run_in_threadpool(read_document, conn, user.id, source)
    except OSError:
        result = ReadResult(error="The file couldn't be read.", detail="stored file unreadable", step="read")
    with transaction(conn):
        save_result(conn, user.id, item_id, run, result)
        item = item_payload(conn, user.id, item_id)
        remember(conn, user.id, key, "inbox.upload", item)
    return item


def item_payload(conn: sqlite3.Connection, owner_id: int, item_id: int) -> dict:
    out = inbox_out(owned_row(conn, "inbox_items", owner_id, item_id))
    out["file"] = file_out(file_row(conn, owner_id, item_id))
    return out


@router.post("/inbox/{item_id}/suggest")
def suggest(item_id: int, body: RejectIn, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    """Run the extractor on a text note."""
    with transaction(conn):
        row = owned_row(conn, "inbox_items", user.id, item_id)
        check_version(row, body.version)
        if row["kind"] != "text" or row["status"] not in ("new", "failed"):
            raise HTTPException(status_code=409, detail=f"Can't get suggestions for an item that is {row['status']}.")
        run = begin_reading(conn, user.id, item_id)
    result = read_document(conn, user.id, Source([row["content"]]))  # outside the transaction (can be slow)
    with transaction(conn):
        save_result(conn, user.id, item_id, run, result)
        return item_payload(conn, user.id, item_id)


@router.post("/inbox/{item_id}/retry")
def retry(item_id: int, request: Request, body: RejectIn, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    with transaction(conn):
        row = owned_row(conn, "inbox_items", user.id, item_id)
        check_version(row, body.version)
        if row["status"] != "failed":
            raise HTTPException(status_code=409, detail="Only failed items can be retried.")
        stored = file_row(conn, user.id, item_id)
        run = workflow.latest(conn, user.id, item_id)
        run = workflow.move(conn, run, "running", step="retry") if run is not None and run["state"] == "failed" else None
        run = begin_reading(conn, user.id, item_id, run)
    try:  # outside the transaction: reading can be slow
        if stored is None:
            source = Source([row["content"]])
        else:
            data = (files_folder(request) / stored["stored_name"]).read_bytes()
            pages = read_pdf(data).pages if stored["media_type"] == "application/pdf" else []
            source = Source(pages, data, stored["media_type"])
    except (DocumentError, OSError) as error:
        reason = str(error) if isinstance(error, DocumentError) else "The file couldn't be read."
        result = ReadResult(error=reason, detail=reason, step="read")
    else:
        result = read_document(conn, user.id, source)
    with transaction(conn):
        save_result(conn, user.id, item_id, run, result)
        return item_payload(conn, user.id, item_id)


@router.get("/inbox/{item_id}/review")
def review(item_id: int, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    out = item_payload(conn, user.id, item_id)
    proposal = conn.execute(
        "SELECT * FROM proposals WHERE owner_id = ? AND inbox_item_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (user.id, item_id),
    ).fetchone()
    out["run"] = workflow.run_out(workflow.latest(conn, user.id, item_id))
    out["proposal"] = (
        {"id": proposal["id"], "extractor": proposal["extractor"], "note": proposal["note"], "fields": json.loads(proposal["fields"])}
        if proposal
        else None
    )
    return out


def to_cents(amount: str | None) -> int | None:
    if amount is None or not amount.strip():
        return None
    try:
        value = Decimal(amount.replace(",", "").strip())
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="Amount must be a number like 1234.50.") from None
    if value < 0 or value > Decimal("1e10"):
        raise HTTPException(status_code=422, detail="Amount is out of range.")
    return int((value * 100).quantize(Decimal("1")))


@router.post("/inbox/{item_id}/accept", status_code=201)
def accept(
    item_id: int,
    body: AcceptIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Save what the user confirmed: record, optional task, proposal status, note status and activity together."""
    key = idempotency_key(request)
    cents = to_cents(body.amount)
    if body.make_task and not (body.task_title or "").strip():
        raise HTTPException(status_code=422, detail="Give the task a name, or untick 'Also make a task'.")
    with transaction(conn):
        earlier = replay(conn, user.id, key, "inbox.accept")
        if earlier is not None:
            return earlier
        row = owned_row(conn, "inbox_items", user.id, item_id)
        check_version(row, body.version)
        if row["status"] != "needs_review":
            raise HTTPException(status_code=409, detail="This item isn't waiting for review any more.")
        proposal = conn.execute(
            "SELECT id FROM proposals WHERE owner_id = ? AND inbox_item_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (user.id, item_id),
        ).fetchone()
        stored = file_row(conn, user.id, item_id)
        now = utc_now()
        cur = conn.execute(
            """INSERT INTO records (owner_id, inbox_item_id, proposal_id, file_id, doc_type, title, supplier, amount_cents,
                                    currency, reference, due_date, expiry_date, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user.id, item_id, proposal["id"] if proposal else None, stored["id"] if stored else None,
                body.doc_type, body.title, body.supplier, cents, body.currency if cents is not None else None,
                body.reference, body.due_date and body.due_date.isoformat(),
                body.expiry_date and body.expiry_date.isoformat(), now, now,
            ),
        )  # fmt: skip
        record_id = cur.lastrowid
        if proposal:
            conn.execute("UPDATE proposals SET status = 'accepted', decided_at = ? WHERE id = ?", (now, proposal["id"]))
        conn.execute(
            "UPDATE inbox_items SET status = 'converted', version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
            (now, item_id, user.id),
        )
        audit(conn, user.id, "record", record_id, "created", "from_review")
        workflow.finish_open(conn, user.id, item_id, "completed", step="accepted")
        task = None
        if body.make_task:
            tcur = conn.execute(
                """INSERT INTO tasks (owner_id, title, details, category, due_date, status, source_inbox_id, source_record_id,
                                      created_at, updated_at) VALUES (?, ?, '', ?, ?, 'open', ?, ?, ?, ?)""",
                (user.id, body.task_title.strip(), body.task_category,
                 body.task_due_date and body.task_due_date.isoformat(), item_id, record_id, now, now),
            )  # fmt: skip
            task = task_out(owned_row(conn, "tasks", user.id, tcur.lastrowid))
            audit(conn, user.id, "task", task["id"], "created", "from_record")
            reminders.sync_task(conn, user.id, task["id"])
        result = {"record": record_out(conn, user.id, record_id), "task": task}
        remember(conn, user.id, key, "inbox.accept", result)
    return result


@router.post("/inbox/{item_id}/reject")
def reject(item_id: int, body: RejectIn, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    with transaction(conn):
        row = owned_row(conn, "inbox_items", user.id, item_id)
        check_version(row, body.version)
        if row["status"] != "needs_review":
            raise HTTPException(status_code=409, detail="This item isn't waiting for review any more.")
        now = utc_now()
        conn.execute(
            "UPDATE proposals SET status = 'rejected', decided_at = ? WHERE owner_id = ? AND inbox_item_id = ? AND status = 'pending'",
            (now, user.id, item_id),
        )
        conn.execute(
            "UPDATE inbox_items SET status = 'new', version = version + 1, updated_at = ? WHERE id = ? AND owner_id = ?",
            (now, item_id, user.id),
        )
        audit(conn, user.id, "inbox", item_id, "proposal_rejected")
        workflow.finish_open(conn, user.id, item_id, "completed", step="rejected")
        return item_payload(conn, user.id, item_id)


def record_out(conn: sqlite3.Connection, owner_id: int, record_id: int) -> dict:
    row = conn.execute("SELECT * FROM records WHERE id = ? AND owner_id = ?", (record_id, owner_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    stored = conn.execute("SELECT * FROM files WHERE id = ? AND owner_id = ?", (row["file_id"], owner_id)).fetchone() if row["file_id"] else None
    return {
        "id": row["id"],
        "doc_type": row["doc_type"],
        "title": row["title"],
        "supplier": row["supplier"],
        "amount": f"{row['amount_cents'] / 100:.2f}" if row["amount_cents"] is not None else None,
        "currency": row["currency"],
        "reference": row["reference"],
        "due_date": row["due_date"],
        "expiry_date": row["expiry_date"],
        "file": file_out(stored),
        "created_at": row["created_at"],
    }


@router.get("/records")
def records(user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    ids = [r["id"] for r in conn.execute("SELECT id FROM records WHERE owner_id = ? ORDER BY created_at DESC, id DESC", (user.id,))]
    return {"records": [record_out(conn, user.id, i) for i in ids], "doc_types": list(DOC_TYPES)}


INLINE_TYPES = {"image/jpeg", "image/png"}  # safe raster images may be shown inside the review page


@router.get("/files/{file_id}")
def download(
    file_id: int,
    request: Request,
    inline: bool = False,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> Response:
    row = conn.execute("SELECT * FROM files WHERE id = ? AND owner_id = ?", (file_id, user.id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Not found.")
    path = files_folder(request) / row["stored_name"]
    try:
        data = path.read_bytes()
    except OSError:
        raise HTTPException(status_code=404, detail="The file is missing from storage.") from None
    disposition = "inline" if inline and row["media_type"] in INLINE_TYPES else "attachment"
    safe_name = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in row["original_name"])[:100] or "document"
    return Response(
        content=data,
        media_type=row["media_type"],
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


def delete_item_files(conn: sqlite3.Connection, request: Request, owner_id: int, item_id: int) -> None:
    """When an inbox item is deleted, its stored file goes too (unless a record still uses it)."""
    row = file_row(conn, owner_id, item_id)
    if row is None:
        return
    used = conn.execute("SELECT 1 FROM records WHERE owner_id = ? AND file_id = ?", (owner_id, row["id"])).fetchone()
    if used:
        return
    conn.execute("DELETE FROM files WHERE id = ? AND owner_id = ?", (row["id"], owner_id))
    with suppress(OSError):
        remove_stored(files_folder(request), row["stored_name"])
