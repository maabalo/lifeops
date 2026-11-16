"""Two-step login with an authenticator app (TOTP) and recovery codes."""

from __future__ import annotations

import base64
import time
from contextlib import closing

from fastapi.testclient import TestClient

from app import totp
from app.db import connect

from .conftest import PASSWORD


def test_code_generator_matches_the_rfc_6238_example():
    # RFC 6238 test vector: ASCII secret "12345678901234567890", T = 59 s → 94287082 (8 digits) → last 6: 287082
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59 // 30) == "287082"


def test_codes_allow_drift_but_never_repeat():
    secret = totp.new_secret()
    now = 1_800_000_000.0
    step = totp.current_step(now)
    assert totp.verify(secret, totp.code_at(secret, step - 1), 0, now) == step - 1  # 30 s late: fine
    assert totp.verify(secret, totp.code_at(secret, step), step, now) is None  # already used
    assert totp.verify(secret, totp.code_at(secret, step - 3), 0, now) is None  # too old
    assert totp.verify(secret, "12345", 0, now) is None


def turn_on(me) -> tuple[str, list[str]]:
    started = me.post("/api/two-step/start", json={"password": PASSWORD}).json()
    assert started["uri"].startswith("otpauth://totp/LifeOps:me?secret=")
    wrong = me.post("/api/two-step/confirm", json={"code": "000000"})
    assert wrong.status_code == 422
    code = totp.code_at(started["secret"], totp.current_step())
    result = me.post("/api/two-step/confirm", json={"code": code}).json()
    return started["secret"], result["recovery_codes"]


def fresh_login(app) -> TestClient:
    return TestClient(app, base_url="http://127.0.0.1:8780")


def wait_for_next_step():
    time.sleep(30 - (time.time() % 30) + 0.2)


def test_turning_on_needs_the_password_and_a_working_code(me):
    assert me.post("/api/two-step/start", json={"password": "wrong"}).status_code == 403
    secret, codes = turn_on(me)
    assert len(codes) == 10 and len(set(codes)) == 10
    info = me.get("/api/me").json()
    assert info["two_step"] is True and info["recovery_codes_left"] == 10


def test_login_then_asks_for_the_code(me, app):
    secret, _ = turn_on(me)
    c = fresh_login(app)
    first = c.post("/api/login", json={"username": "me", "password": PASSWORD}).json()
    assert first["two_step"] is True and "csrf_token" not in first
    assert c.get("/api/me").status_code == 401  # the password alone doesn't log you in
    assert c.post("/api/login/code", json={"ticket": first["ticket"], "code": "000000"}).status_code == 401
    wait_for_next_step()  # the confirm step used the current code: wait for a fresh one
    code = totp.code_at(secret, totp.current_step())
    ok = c.post("/api/login/code", json={"ticket": first["ticket"], "code": code})
    assert ok.status_code == 200 and c.get("/api/me").json()["username"] == "me"
    again = fresh_login(app)
    ticket = again.post("/api/login", json={"username": "me", "password": PASSWORD}).json()["ticket"]
    assert again.post("/api/login/code", json={"ticket": ticket, "code": code}).status_code == 401  # never twice


def test_a_ticket_stops_after_five_wrong_codes(me, app):
    turn_on(me)
    c = fresh_login(app)
    ticket = c.post("/api/login", json={"username": "me", "password": PASSWORD}).json()["ticket"]
    for _ in range(5):
        c.post("/api/login/code", json={"ticket": ticket, "code": "000000"})
    response = c.post("/api/login/code", json={"ticket": ticket, "code": "000000"})
    assert response.status_code in (401, 429)
    assert "expired" in response.json()["detail"] or "Too many" in response.json()["detail"]


def test_recovery_codes_work_once(me, app):
    _, codes = turn_on(me)
    c = fresh_login(app)
    ticket = c.post("/api/login", json={"username": "me", "password": PASSWORD}).json()["ticket"]
    assert c.post("/api/login/code", json={"ticket": ticket, "code": codes[0].upper()}).status_code == 200
    assert c.get("/api/me").json()["recovery_codes_left"] == 9
    c2 = fresh_login(app)
    ticket2 = c2.post("/api/login", json={"username": "me", "password": PASSWORD}).json()["ticket"]
    assert c2.post("/api/login/code", json={"ticket": ticket2, "code": codes[0]}).status_code == 401


def test_turning_off_needs_password_and_code(me):
    secret, codes = turn_on(me)
    assert me.post("/api/two-step/off", json={"password": PASSWORD, "code": "000000"}).status_code == 403
    assert me.post("/api/two-step/off", json={"password": "wrong", "code": codes[1]}).status_code == 403
    assert me.post("/api/two-step/off", json={"password": PASSWORD, "code": codes[1]}).json() == {"ok": True}
    assert me.get("/api/me").json()["two_step"] is False


def test_lockout_command_turns_it_off(me, app, monkeypatch):
    from app import manage

    turn_on(me)
    assert manage.main(["disable-2fa", "me"]) == 0
    with closing(connect(app.state.settings)) as conn:
        assert conn.execute("SELECT totp_secret FROM users WHERE username = 'me'").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM recovery_codes").fetchone()[0] == 0
