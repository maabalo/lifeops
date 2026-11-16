"""Login, sessions, CSRF, rate limiting and security headers."""

from fastapi.testclient import TestClient

from app.security import LOGIN_MAX_FAILURES

from .conftest import PASSWORD, make_user


def client(app):
    return TestClient(app, base_url="http://127.0.0.1:8780")


def test_private_routes_need_login(app):
    c = client(app)
    for url in ["/api/me", "/api/inbox", "/api/tasks", "/api/today"]:
        assert c.get(url).status_code == 401, url


def test_login_sets_a_safe_cookie_and_logout_ends_it(app):
    make_user("me")
    c = client(app)
    response = c.post("/api/login", json={"username": "me", "password": PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    csrf = response.json()["csrf_token"]
    assert c.get("/api/me").json()["username"] == "me"
    assert c.post("/api/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/me").status_code == 401


def test_wrong_password_and_unknown_username_get_the_same_answer(app):
    make_user("me")
    c = client(app)
    wrong = c.post("/api/login", json={"username": "me", "password": "nope"})
    unknown = c.post("/api/login", json={"username": "nobody", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_login_is_rate_limited(app):
    make_user("me")
    c = client(app)
    for _ in range(LOGIN_MAX_FAILURES):
        c.post("/api/login", json={"username": "me", "password": "wrong"})
    blocked = c.post("/api/login", json={"username": "me", "password": PASSWORD})
    assert blocked.status_code == 429


def test_changes_need_the_csrf_token_and_our_origin(me):
    no_token = me.client.post("/api/tasks", json={"title": "x"})
    assert no_token.status_code == 403
    other_site = me.client.post(
        "/api/tasks", json={"title": "x"}, headers=me.headers({"Origin": "https://evil.example"})
    )
    assert other_site.status_code == 403
    login_from_other_site = me.client.post(
        "/api/login", json={"username": "me", "password": PASSWORD}, headers={"Origin": "https://evil.example"}
    )
    assert login_from_other_site.status_code == 403
    assert me.post("/api/tasks", json={"title": "x"}).status_code == 201


def test_security_headers_and_no_cache_for_personal_data(me):
    page = me.client.get("/")
    assert page.status_code == 200
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    api = me.get("/api/today")
    assert api.headers["cache-control"] == "no-store"


def test_big_requests_are_refused(me):
    response = me.post("/api/inbox", json={"content": "x" * 70000})
    assert response.status_code == 413


def test_timezone_setting(me):
    assert me.patch("/api/me", json={"timezone": "Europe/Berlin"}).json()["timezone"] == "Europe/Berlin"
    assert me.patch("/api/me", json={"timezone": "Mars/Base"}).status_code == 422


def local_client(app, address="127.0.0.1"):
    return TestClient(app, base_url="http://127.0.0.1:8780", client=(address, 50000))


def test_first_account_can_be_created_on_this_computer_only_once(app):
    c = local_client(app)
    assert c.get("/api/setup").json() == {"needed": True}
    made = c.post("/api/setup", json={"username": "alex", "password": "a long enough password"})
    assert made.status_code == 201
    assert c.get("/api/me").json()["username"] == "alex"  # logged in straight away
    assert c.get("/api/setup").json() == {"needed": False}
    again = local_client(app).post("/api/setup", json={"username": "someone_else", "password": "another long password"})
    assert again.status_code == 409


def test_setup_is_refused_from_another_computer_and_checks_input(app):
    remote = local_client(app, "203.0.113.9")
    assert remote.get("/api/setup").json() == {"needed": False}
    assert remote.post("/api/setup", json={"username": "alex", "password": "a long enough password"}).status_code == 403
    c = local_client(app)
    assert c.post("/api/setup", json={"username": "alex", "password": "short"}).status_code == 422
    assert c.post("/api/setup", json={"username": "no spaces allowed", "password": "a long enough password"}).status_code == 422
    behind_proxy = TestClient(app, base_url="https://lifeops.example.com", client=("127.0.0.1", 50000))
    assert behind_proxy.get("/api/setup").json() == {"needed": False}
    proxied = behind_proxy.post(
        "/api/setup",
        json={"username": "alex", "password": "a long enough password"},
        headers={"Origin": "https://lifeops.example.com"},
    )
    assert proxied.status_code == 403  # a visitor through a proxy on the same machine can't claim it
    other_site = c.post(
        "/api/setup",
        json={"username": "alex", "password": "a long enough password"},
        headers={"Origin": "https://evil.example"},
    )
    assert other_site.status_code == 403
