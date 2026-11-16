"""
LifeOps security: passwords, sessions, CSRF protection and login rate-limiting (one place for all of it).

- Passwords: Argon2id (argon2-cffi). Never stored or logged in plain text.
- Sessions: a random token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored, with an expiry.
- CSRF: every change (POST/PATCH/DELETE) needs the session's CSRF token in the X-CSRF-Token header, and an
  Origin (when the browser sends one) matching this server. Other websites can't read the token.
- Login: at most LOGIN_MAX_FAILURES failed attempts per address per LOGIN_WINDOW; same answer for a wrong username
  or a wrong password, so the login form doesn't reveal which usernames exist.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response

from .db import utc_now

SESSION_COOKIE = "lifeops_session"
CSRF_HEADER = "X-CSRF-Token"
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW = 15 * 60  # seconds
MIN_PASSWORD_LENGTH = 12
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

_hasher = PasswordHasher()
# A real hash of a random password: checking it when the username is unknown keeps both answers equally slow.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


# ---- passwords ------------------------------------------------------------------------------------
def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Use at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---- login rate limit (in memory: fine for one small server) ----------------------------------------
class LoginLimiter:
    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, address: str) -> bool:
        with self._lock:
            recent = [t for t in self._failures.get(address, []) if time.monotonic() - t < LOGIN_WINDOW]
            self._failures[address] = recent
            return len(recent) >= LOGIN_MAX_FAILURES

    def failed(self, address: str) -> None:
        with self._lock:
            self._failures.setdefault(address, []).append(time.monotonic())

    def succeeded(self, address: str) -> None:
        with self._lock:
            self._failures.pop(address, None)


login_limiter = LoginLimiter()


# ---- sessions -------------------------------------------------------------------------------------
def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, user_id: int, days: int) -> tuple[str, str]:
    """A new login. Returns (session token for the cookie, CSRF token for the page)."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, csrf_token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        (_digest(token), user_id, csrf, utc_now(), expires.isoformat().replace("+00:00", "Z")),
    )
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (utc_now(),))  # tidy up old logins
    return token, csrf


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))


def set_session_cookie(response: Response, token: str, days: int, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=days * 86400,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=secure, samesite="strict")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    timezone: str
    csrf_token: str


def get_conn(request: Request) -> sqlite3.Connection:
    """One SQLite connection per request (closed by the middleware in main.py)."""
    return request.state.conn


def current_user(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> CurrentUser:
    """Who is calling: from the session cookie only (never from anything the browser sends in the body).
    Changes also need a matching CSRF token and origin."""
    token = request.cookies.get(SESSION_COOKIE)
    row = None
    if token:
        row = conn.execute(
            """SELECT users.id, users.username, users.timezone, sessions.csrf_token
               FROM sessions JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (_digest(token), utc_now()),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Please log in.")
    user = CurrentUser(id=row["id"], username=row["username"], timezone=row["timezone"], csrf_token=row["csrf_token"])
    if request.method not in SAFE_METHODS:
        check_csrf(request, user.csrf_token)
    return user


def check_csrf(request: Request, expected: str) -> None:
    sent = request.headers.get(CSRF_HEADER, "")
    if not hmac.compare_digest(sent, expected):
        raise HTTPException(status_code=403, detail="This page's session is out of date. Reload and try again.")
    check_origin(request)


def check_origin(request: Request) -> None:
    """When the browser says where a change came from, it must be this server."""
    origin = request.headers.get("origin")
    if origin is None:
        return
    host = request.headers.get("host", "")
    if origin not in {f"http://{host}", f"https://{host}"}:
        raise HTTPException(status_code=403, detail="Requests from other websites are not allowed.")
