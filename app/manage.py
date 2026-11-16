"""
LifeOps account tool (Terminal only; there is no public sign-up page).

    .venv/bin/python -m app.manage create-user alex               # asks for a password (hidden)
    .venv/bin/python -m app.manage set-password alex
    .venv/bin/python -m app.manage logout-everywhere alex
    .venv/bin/python -m app.manage disable-2fa alex               # locked out of the authenticator app
    .venv/bin/python -m app.manage backup                          # → backups/lifeops-<date-time>/
    .venv/bin/python -m app.manage restore backups/lifeops-<date-time>   # stop the server first
"""

from __future__ import annotations

import getpass
import re
import sys
from contextlib import closing

from pathlib import Path

from . import backup
from .db import connect, migrate, transaction, utc_now
from .security import MIN_PASSWORD_LENGTH, hash_password
from .settings import load_settings


def ask_password() -> str:
    first = getpass.getpass(f"New password (at least {MIN_PASSWORD_LENGTH} characters): ")
    if getpass.getpass("Same password again: ") != first:
        raise SystemExit("The two passwords don't match.")
    return first


def main(argv: list[str]) -> int:
    if argv[:1] == ["backup"] and len(argv) == 1:
        folder = backup.backup(load_settings())
        print(f"Backup saved and checked: {folder}")
        return 0
    if argv[:1] == ["restore"] and len(argv) == 2:
        aside = backup.restore(load_settings(), Path(argv[1]).resolve())
        print(f"Restored. Your previous data was kept in {aside}")
        return 0
    if len(argv) != 2 or argv[0] not in {"create-user", "set-password", "logout-everywhere", "disable-2fa"}:
        print(__doc__)
        return 2
    command, username = argv
    settings = load_settings()
    with closing(connect(settings)) as conn:
        migrate(conn)
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if command == "create-user":
            if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", username):
                raise SystemExit("Use 3-32 letters, numbers, dots, dashes or underscores, e.g. alex.")
            if row:
                raise SystemExit("That account already exists.")
            password_hash = hash_password(ask_password())
            conn.execute(
                "INSERT INTO users (username, password_hash, timezone, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, settings.default_timezone, utc_now()),
            )
            print(f"Account created. Timezone: {settings.default_timezone} (change it in Settings).")
            return 0
        if not row:
            raise SystemExit("No account with that username.")
        with transaction(conn):
            if command == "disable-2fa":
                conn.execute("UPDATE users SET totp_secret = NULL, totp_pending = NULL, totp_last_step = 0 WHERE id = ?", (row["id"],))
                conn.execute("DELETE FROM recovery_codes WHERE owner_id = ?", (row["id"],))
                print("Two-step login is off for this account.")
            if command == "set-password":
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(ask_password()), row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        print("Done. Every device has been logged out.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
