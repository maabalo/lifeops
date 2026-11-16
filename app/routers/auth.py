"""Login, logout and "who am I" (the page also gets its CSRF token here)."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..models import CodeIn, LoginIn, PasswordIn, SettingsIn, SetupIn, TwoStepConfirmIn, TwoStepOffIn
from ..security import (
    SESSION_COOKIE,
    CurrentUser,
    check_origin,
    clear_session_cookie,
    create_session,
    current_user,
    end_session,
    get_conn,
    hash_password,
    login_limiter,
    set_session_cookie,
    verify_password,
)
from .. import reminders, totp
from ..db import transaction, utc_now
from ..extractor import claude_available
from ..local_reader import local_model
from ..services import audit

router = APIRouter(prefix="/api", tags=["auth"])


def client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# The first account can be made from the page only on this computer: once LifeOps is online, nobody else can
# claim an empty install. After one account exists, setup is closed for good.
LOCAL_ADDRESSES = {"127.0.0.1", "::1", "localhost"}


def setup_needed(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def is_local(request: Request) -> bool:
    """From this computer AND opened as 127.0.0.1/localhost. Checking the Host too matters online: behind a proxy
    on the same machine every visitor arrives from 127.0.0.1, but with the public site name as the Host."""
    host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
    return client_address(request) in LOCAL_ADDRESSES and host in LOCAL_ADDRESSES


@router.get("/setup")
def setup_status(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Does the page need to show "Create your account"?"""
    return {"needed": setup_needed(conn) and is_local(request)}


@router.post("/setup", status_code=201)
def setup(body: SetupIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Create the first account and log in. Refused once any account exists, or from another computer."""
    check_origin(request)
    if not is_local(request):
        raise HTTPException(status_code=403, detail="The first account can only be created on the computer running LifeOps.")
    settings = request.app.state.settings
    password_hash = hash_password(body.password)  # slow on purpose: done before the transaction
    with transaction(conn):
        if not setup_needed(conn):  # checked inside the transaction, so two tabs can't both create one
            raise HTTPException(status_code=409, detail="An account already exists. Log in instead.")
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at) VALUES (?, ?, ?, ?)",
            (body.username, password_hash, settings.default_timezone, utc_now()),
        )
        user_id = cur.lastrowid
        audit(conn, user_id, "account", None, "created")
        return start_session(conn, request, response, user_id)


TICKET_MINUTES = 5
TICKET_ATTEMPTS = 5


def start_session(conn: sqlite3.Connection, request: Request, response: Response, user_id: int) -> dict:
    settings = request.app.state.settings
    token, csrf = create_session(conn, user_id, settings.session_days)
    audit(conn, user_id, "session", None, "login")
    set_session_cookie(response, token, settings.session_days, settings.cookie_secure)
    return {"ok": True, "csrf_token": csrf}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Step 1: the password. With two-step login on, it returns a short-lived ticket for step 2 instead."""
    check_origin(request)
    address = client_address(request)
    if login_limiter.blocked(address):
        raise HTTPException(status_code=429, detail="Too many tries. Wait 15 minutes, then try again.")
    row = conn.execute("SELECT id, password_hash, totp_secret FROM users WHERE username = ?", (body.username,)).fetchone()
    if not verify_password(row["password_hash"] if row else None, body.password):
        login_limiter.failed(address)
        raise HTTPException(status_code=401, detail="That username and password don't match.")
    if row["totp_secret"]:
        ticket = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(minutes=TICKET_MINUTES)).replace(microsecond=0)
        conn.execute("DELETE FROM login_tickets WHERE expires_at < ?", (utc_now(),))
        conn.execute(
            "INSERT INTO login_tickets (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (sha256(ticket), row["id"], expires.isoformat().replace("+00:00", "Z")),
        )
        return {"two_step": True, "ticket": ticket}
    login_limiter.succeeded(address)
    return start_session(conn, request, response, row["id"])


@router.post("/login/code")
def login_code(body: CodeIn, request: Request, response: Response, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Step 2: the 6-digit code from the authenticator app, or a one-time recovery code."""
    check_origin(request)
    address = client_address(request)
    if login_limiter.blocked(address):
        raise HTTPException(status_code=429, detail="Too many tries. Wait 15 minutes, then try again.")
    with transaction(conn):
        ticket = conn.execute(
            "SELECT * FROM login_tickets WHERE token_hash = ? AND expires_at > ?", (sha256(body.ticket), utc_now())
        ).fetchone()
        if ticket is None or ticket["attempts"] >= TICKET_ATTEMPTS:
            raise HTTPException(status_code=401, detail="That login has expired. Enter your password again.")
        user = conn.execute("SELECT id, totp_secret, totp_last_step FROM users WHERE id = ?", (ticket["user_id"],)).fetchone()
        used = check_second_step(conn, user, body.code)
        if not used:
            conn.execute("UPDATE login_tickets SET attempts = attempts + 1 WHERE token_hash = ?", (ticket["token_hash"],))
            login_limiter.failed(address)
            wrong = True
        else:
            conn.execute("DELETE FROM login_tickets WHERE token_hash = ?", (ticket["token_hash"],))
            wrong = False
    if wrong:  # outside the transaction, so the attempt count is saved
        raise HTTPException(status_code=401, detail="That code isn't right. Check the app and try again.")
    login_limiter.succeeded(address)
    if used == "recovery":
        audit(conn, user["id"], "two_step", None, "recovery_code_used")
    return start_session(conn, request, response, user["id"])


def check_second_step(conn: sqlite3.Connection, user: sqlite3.Row, code: str) -> str | None:
    """'totp' or 'recovery' when the code is right (and marks it used), else None."""
    if user["totp_secret"]:
        step = totp.verify(user["totp_secret"], code, user["totp_last_step"])
        if step is not None:
            conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step, user["id"]))
            return "totp"
    row = conn.execute(
        "SELECT id FROM recovery_codes WHERE owner_id = ? AND code_hash = ? AND used_at IS NULL",
        (user["id"], totp.recovery_hash(code)),
    ).fetchone()
    if row is not None:
        conn.execute("UPDATE recovery_codes SET used_at = ? WHERE id = ?", (utc_now(), row["id"]))
        return "recovery"
    return None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---- turning two-step login on and off (Settings) -------------------------------------------------
def require_password(conn: sqlite3.Connection, request: Request, user: CurrentUser, password: str) -> None:
    address = f"password:{client_address(request)}"
    if login_limiter.blocked(address):
        raise HTTPException(status_code=429, detail="Too many tries. Wait 15 minutes, then try again.")
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()
    if not verify_password(row["password_hash"], password):
        login_limiter.failed(address)
        raise HTTPException(status_code=403, detail="That password isn't right.")


@router.post("/two-step/start")
def two_step_start(body: PasswordIn, request: Request, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    """Make a new secret (not active until confirmed with a code) and the link for the QR code."""
    require_password(conn, request, user, body.password)
    secret = totp.new_secret()
    conn.execute("UPDATE users SET totp_pending = ? WHERE id = ?", (secret, user.id))
    return {"secret": secret, "uri": totp.otpauth_uri(secret, user.username)}


@router.post("/two-step/confirm")
def two_step_confirm(body: TwoStepConfirmIn, request: Request, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    """The first code from the app proves it was set up right: switch on, and hand out recovery codes once."""
    address = f"password:{client_address(request)}"  # wrong codes count like wrong passwords
    if login_limiter.blocked(address):
        raise HTTPException(status_code=429, detail="Too many tries. Wait 15 minutes, then try again.")
    with transaction(conn):
        row = conn.execute("SELECT totp_pending FROM users WHERE id = ?", (user.id,)).fetchone()
        if not row["totp_pending"]:
            raise HTTPException(status_code=409, detail="Start the setup again.")
        step = totp.verify(row["totp_pending"], body.code, 0)
        if step is None:
            login_limiter.failed(address)
            raise HTTPException(status_code=422, detail="That code isn't right. Check the app's clock and try the newest code.")
        codes = totp.new_recovery_codes()
        conn.execute(
            "UPDATE users SET totp_secret = totp_pending, totp_pending = NULL, totp_last_step = ? WHERE id = ?",
            (step, user.id),
        )
        conn.execute("DELETE FROM recovery_codes WHERE owner_id = ?", (user.id,))
        conn.executemany(
            "INSERT INTO recovery_codes (owner_id, code_hash) VALUES (?, ?)", [(user.id, totp.recovery_hash(c)) for c in codes]
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND csrf_token != ?", (user.id, user.csrf_token))  # other devices log in again
        audit(conn, user.id, "two_step", None, "turned_on")
    return {"ok": True, "recovery_codes": codes}


@router.post("/two-step/off")
def two_step_off(body: TwoStepOffIn, request: Request, user=Depends(current_user), conn=Depends(get_conn)) -> dict:
    require_password(conn, request, user, body.password)
    with transaction(conn):
        row = conn.execute("SELECT id, totp_secret, totp_last_step FROM users WHERE id = ?", (user.id,)).fetchone()
        if not row["totp_secret"]:
            raise HTTPException(status_code=409, detail="Two-step login is already off.")
        if not check_second_step(conn, row, body.code):
            login_limiter.failed(f"password:{client_address(request)}")
            raise HTTPException(status_code=403, detail="That code isn't right.")
        conn.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, totp_last_step = 0 WHERE id = ?", (user.id,))
        conn.execute("DELETE FROM recovery_codes WHERE owner_id = ?", (user.id,))
        audit(conn, user.id, "two_step", None, "turned_off")
    return {"ok": True}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: CurrentUser = Depends(current_user),
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    end_session(conn, request.cookies.get(SESSION_COOKIE))
    audit(conn, user.id, "session", None, "logout")
    clear_session_cookie(response, request.app.state.settings.cookie_secure)
    return {"ok": True}


def me_out(conn: sqlite3.Connection, user: CurrentUser) -> dict:
    row = conn.execute("SELECT timezone, reminder_time, extractor, totp_secret FROM users WHERE id = ?", (user.id,)).fetchone()
    left = conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE owner_id = ? AND used_at IS NULL", (user.id,)).fetchone()[0]
    return {
        "username": user.username,
        "timezone": row["timezone"],
        "reminder_time": row["reminder_time"],
        "extractor": row["extractor"],
        "claude_available": claude_available(),
        "local_model": local_model(),  # the Ollama model on this computer, or None
        "two_step": bool(row["totp_secret"]),
        "recovery_codes_left": left,
        "csrf_token": user.csrf_token,
    }


@router.get("/me")
def me(user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return me_out(conn, user)


@router.patch("/me")
def update_me(
    body: SettingsIn, user: CurrentUser = Depends(current_user), conn: sqlite3.Connection = Depends(get_conn)
) -> dict:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if body.timezone is not None:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(status_code=422, detail="Unknown timezone. Use a name like Asia/Manila.") from None
    if body.extractor == "claude" and not claude_available():
        raise HTTPException(status_code=409, detail="Claude isn't set up yet: an ANTHROPIC_API_KEY is needed on the server.")
    if body.extractor == "local" and not local_model():
        raise HTTPException(status_code=409, detail="Ollama isn't running on this computer, or has no model yet.")
    with transaction(conn):
        for column, value, action in (
            ("timezone", body.timezone, "timezone_changed"),
            ("reminder_time", body.reminder_time, "reminder_time_changed"),
            ("extractor", body.extractor, "reader_changed"),
        ):
            if value is not None:
                conn.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (value, user.id))  # noqa: S608
                audit(conn, user.id, "settings", None, action, value if column == "extractor" else "")
        if body.timezone is not None or body.reminder_time is not None:
            reminders.sync_all(conn, user.id)
    return me_out(conn, user)
