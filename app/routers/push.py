"""Push reminders (M4): the public key, subscribe / unsubscribe a device, send a test."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from ..db import transaction, utc_now
from ..models import Strict
from ..push import allowed_endpoint, public_key, send_to_owner, vapid_key
from ..security import CurrentUser, current_user, get_conn
from ..services import audit

router = APIRouter(prefix="/api/push", tags=["push"])


class Keys(Strict):
    p256dh: str = Field(min_length=20, max_length=200, pattern=r"^[A-Za-z0-9_-]+=*$")
    auth: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+=*$")


class SubscribeIn(Strict):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: Keys
    expirationTime: int | None = None  # noqa: N815 (the browser's own field name)


class UnsubscribeIn(Strict):
    endpoint: str = Field(min_length=10, max_length=1000)


@router.get("/key")
def key(request: Request, user: CurrentUser = Depends(current_user)) -> dict:
    return {"public_key": public_key(vapid_key(request.app.state.settings.data_dir))}


@router.post("/subscribe", status_code=201)
def subscribe(body: SubscribeIn, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    if not allowed_endpoint(body.endpoint):
        raise HTTPException(status_code=422, detail="That isn't a known push service address.")
    with transaction(conn):
        existing = conn.execute("SELECT owner_id FROM push_subscriptions WHERE endpoint = ?", (body.endpoint,)).fetchone()
        if existing and existing["owner_id"] != user.id:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (body.endpoint,))  # the device changed hands
        conn.execute(
            """INSERT INTO push_subscriptions (owner_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth""",
            (user.id, body.endpoint, body.keys.p256dh, body.keys.auth, utc_now()),
        )
        audit(conn, user.id, "push", None, "subscribed")
        count = conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE owner_id = ?", (user.id,)).fetchone()[0]
    return {"ok": True, "devices": count}


@router.post("/unsubscribe")
def unsubscribe(body: UnsubscribeIn, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    conn.execute("DELETE FROM push_subscriptions WHERE owner_id = ? AND endpoint = ?", (user.id, body.endpoint))
    audit(conn, user.id, "push", None, "unsubscribed")
    return {"ok": True}


@router.get("/status")
def status(user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    count = conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE owner_id = ?", (user.id,)).fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM reminders WHERE owner_id = ? AND state = 'pending'", (user.id,)).fetchone()[0]
    return {"devices": count, "pending_reminders": pending}


@router.post("/test")
def test(request: Request, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    payload = {"title": "LifeOps", "body": "Reminders are on for this device.", "url": "/#/settings", "tag": "lifeops-test"}
    delivered = send_to_owner(conn, user.id, payload, request.app.state.push_sender())
    if delivered == 0:
        raise HTTPException(status_code=409, detail="No device received it. Turn reminders on again on this device.")
    return {"ok": True, "delivered": delivered}
