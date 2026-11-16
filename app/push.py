"""
LifeOps push notifications (Web Push with VAPID keys).

- The VAPID private key comes from LIFEOPS_VAPID_PRIVATE_KEY (PEM) when set (online), otherwise it is created
  once in <data>/vapid_private.pem (git-ignored, readable only by you). It is never in code or logs.
- Subscriptions are only accepted for real push services (ALLOWED_PUSH_HOSTS): the server makes a request to
  that address, so an arbitrary URL would let it be pointed at other machines (SSRF).
- Notification text is neutral ("You have 2 things due today"): it shows on the lock screen.
- Subscriptions the push service reports as gone (404 / 410) are removed.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02

from .db import utc_now

VAPID_FILE = "vapid_private.pem"
DEFAULT_SUBJECT = "mailto:reminders@lifeops.invalid"  # never the user's own email
PUSH_TTL = 12 * 3600  # seconds a push service keeps an undelivered message
ALLOWED_PUSH_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    "web.push.apple.com",
    "notify.windows.com",
)

# A sender takes (subscription row, payload) and returns an HTTP-like status (201 ok, 404/410 gone, 0 error).
Sender = Callable[[sqlite3.Row, dict], int]


def allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_PUSH_HOSTS)


def vapid_key(data_dir: Path) -> Vapid02:
    """The server's VAPID key: from the environment, or created once and kept privately in the data folder."""
    pem = os.environ.get("LIFEOPS_VAPID_PRIVATE_KEY")
    if pem:
        return Vapid02.from_pem(pem.encode())
    path = data_dir / VAPID_FILE
    if not path.is_file():
        data_dir.mkdir(parents=True, exist_ok=True)
        key = Vapid02()
        key.generate_keys()
        key.save_key(str(path))
        path.chmod(0o600)
    return Vapid02.from_file(str(path))


def public_key(key: Vapid02) -> str:
    """The applicationServerKey the browser needs (uncompressed P-256 point, base64url without padding)."""
    raw = key.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def make_sender(data_dir: Path) -> Sender:
    """The real sender (pywebpush). Tests replace it with a fake."""
    from pywebpush import WebPushException, webpush

    key = vapid_key(data_dir)
    subject = os.environ.get("LIFEOPS_VAPID_SUBJECT", DEFAULT_SUBJECT)

    def send(sub: sqlite3.Row, payload: dict) -> int:
        try:
            response = webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload),
                vapid_private_key=key,
                vapid_claims={"sub": subject},
                ttl=PUSH_TTL,
                timeout=10,
            )
            return getattr(response, "status_code", 201)
        except WebPushException as error:
            return error.response.status_code if error.response is not None else 0
        except Exception:  # network problems: report, never crash the scheduler
            return 0

    return send


def send_to_owner(conn: sqlite3.Connection, owner_id: int, payload: dict, sender: Sender) -> int:
    """Send to every device of one owner. Returns how many accepted it; removes devices that are gone."""
    delivered = 0
    for sub in conn.execute("SELECT * FROM push_subscriptions WHERE owner_id = ?", (owner_id,)).fetchall():
        status = sender(sub, payload)
        if status in (404, 410):
            conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
        elif 200 <= status < 300:
            delivered += 1
            conn.execute("UPDATE push_subscriptions SET last_ok_at = ? WHERE id = ?", (utc_now(), sub["id"]))
    return delivered
