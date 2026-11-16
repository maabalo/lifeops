"""
LifeOps two-step login: time-based one-time codes (TOTP, RFC 6238), the kind Google Authenticator shows.

Standard library only. 6 digits, a new code every 30 seconds, SHA-1 (what authenticator apps expect).
A code is accepted for the current 30 seconds and one step either side (clock drift), and never twice:
the last step used is remembered (replay protection).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
DRIFT_STEPS = 1
RECOVERY_CODES = 10


def new_secret() -> str:
    """A random 160-bit secret in base32 (what the QR code and the "enter key manually" option carry)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // PERIOD)


def verify(secret: str, code: str, last_step: int, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None if it's wrong or was already used."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if candidate > last_step and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def otpauth_uri(secret: str, account: str, issuer: str = "LifeOps") -> str:
    """The link inside the setup QR code (authenticator apps read it)."""
    label = f"{quote(issuer)}:{quote(account)}"  # each part encoded; the colon between them stays literal
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"


def new_recovery_codes() -> list[str]:
    """Ten one-time codes like 'k7m2-q9xa' (shown once; only their SHA-256 is stored)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alikes (0/o, 1/l/i)
    return ["-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2)) for _ in range(RECOVERY_CODES)]


def recovery_hash(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
