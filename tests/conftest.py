"""Test setup: every test gets a fresh temporary data folder (database + files) and its own app."""

from __future__ import annotations

from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app import security
from app.db import connect, migrate, utc_now
from app.main import create_app
from app.settings import load_settings

PASSWORD = "correct horse battery staple"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFEOPS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LIFEOPS_TIMEZONE", "Asia/Manila")
    monkeypatch.setenv("LIFEOPS_SCHEDULER_SECONDS", "0")  # tests run the scheduler by hand
    monkeypatch.setenv("LIFEOPS_OLLAMA_URL", "http://not-this-computer.invalid")  # never reach a real Ollama
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    security.login_limiter = security.LoginLimiter()  # fresh rate limit per test
    monkeypatch.setattr("app.routers.auth.login_limiter", security.login_limiter)
    application = create_app()
    application.state.sent = []  # what the fake push sender "delivered"

    def fake_sender(sub, payload):
        application.state.sent.append((sub["endpoint"], payload))
        return 410 if "gone" in sub["endpoint"] else 201

    application.state.push_sender = lambda: fake_sender
    return application


def make_user(username: str) -> int:
    with closing(connect(load_settings())) as conn:
        migrate(conn)
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at) VALUES (?, ?, 'Asia/Manila', ?)",
            (username, security.hash_password(PASSWORD), utc_now()),
        )
        return cur.lastrowid


class User:
    """A logged-in test client that sends the CSRF token on every change."""

    def __init__(self, app, username: str) -> None:
        make_user(username)
        self.client = TestClient(app, base_url="http://127.0.0.1:8780")
        response = self.client.post("/api/login", json={"username": username, "password": PASSWORD})
        assert response.status_code == 200, response.text
        self.csrf = response.json()["csrf_token"]

    def headers(self, extra: dict | None = None) -> dict:
        return {"X-CSRF-Token": self.csrf, **(extra or {})}

    def get(self, url):
        return self.client.get(url)

    def post(self, url, json=None, headers=None):
        return self.client.post(url, json=json, headers=self.headers(headers))

    def patch(self, url, json=None):
        return self.client.patch(url, json=json, headers=self.headers())

    def delete(self, url):
        return self.client.delete(url, headers=self.headers())


@pytest.fixture()
def me(app):
    return User(app, "me")


@pytest.fixture()
def other(app):
    return User(app, "other")
