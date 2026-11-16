"""Today's dashboard: bills due soon (with totals) and things expiring soon, from the user's Records."""

from __future__ import annotations

from contextlib import closing
from datetime import date, timedelta

from app.db import connect


def add_record(conn, owner, doc_type, title, cents=None, currency=None, due=None, expiry=None):
    cur = conn.execute(
        """INSERT INTO records (owner_id, doc_type, title, supplier, amount_cents, currency, due_date, expiry_date,
                                created_at, updated_at) VALUES (?, ?, ?, 'Supplier', ?, ?, ?, ?, 'now', 'now')""",
        (owner, doc_type, title, cents, currency, due, expiry),
    )
    return cur.lastrowid


def owner_id(conn, name):
    return conn.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def test_bills_due_and_their_total(me, other, app):
    today = date.fromisoformat(me.get("/api/today").json()["today"])
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    with closing(connect(app.state.settings)) as conn:
        mine, theirs = owner_id(conn, "me"), owner_id(conn, "other")
        add_record(conn, mine, "bill", "Water", 54500, "PHP", d(10))
        add_record(conn, mine, "bill", "Power", 234560, "PHP", d(2))
        add_record(conn, mine, "bill", "Late phone", 99900, "PHP", d(-5))  # overdue, unpaid: still shown
        add_record(conn, mine, "bill", "Cloud", 2000, "USD", d(20))
        add_record(conn, mine, "bill", "Far away", 100, "PHP", d(45))  # beyond 30 days
        add_record(conn, mine, "bill", "Ancient", 100, "PHP", d(-40))  # too old
        add_record(conn, mine, "receipt", "Not a bill", 100, "PHP", d(3))
        paid = add_record(conn, mine, "bill", "Paid already", 5000, "PHP", d(4))
        conn.execute(
            """INSERT INTO tasks (owner_id, title, category, status, due_date, source_record_id, created_at, updated_at,
                                  completed_at) VALUES (?, 'Pay', 'bills', 'done', ?, ?, 'now', 'now', 'now')""",
            (mine, d(4), paid),
        )
        add_record(conn, theirs, "bill", "Someone else's", 100000, "PHP", d(3))
    data = me.get("/api/today").json()
    assert [b["title"] for b in data["bills_due"]] == ["Late phone", "Power", "Water", "Cloud"]
    assert data["bills_due_count"] == 4
    assert data["bills_total"] == [{"currency": "PHP", "amount": "3889.60"}, {"currency": "USD", "amount": "20.00"}]


def test_expiring_soon_is_sorted_and_private(me, other, app):
    today = date.fromisoformat(me.get("/api/today").json()["today"])
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731
    with closing(connect(app.state.settings)) as conn:
        mine, theirs = owner_id(conn, "me"), owner_id(conn, "other")
        add_record(conn, mine, "warranty", "Aircon warranty", expiry=d(60))
        add_record(conn, mine, "renewal", "Car registration", expiry=d(5))
        add_record(conn, mine, "warranty", "Already ended", expiry=d(-1))
        add_record(conn, mine, "warranty", "Years away", expiry=d(400))
        add_record(conn, theirs, "warranty", "Not mine", expiry=d(10))
    data = me.get("/api/today").json()
    assert [r["title"] for r in data["expiring"]] == ["Car registration", "Aircon warranty"]
    assert set(data["expiring"][0]) == {"id", "doc_type", "title", "supplier", "expiry_date"}


def test_empty_dashboard(me):
    data = me.get("/api/today").json()
    assert data["bills_due"] == [] and data["bills_total"] == [] and data["expiring"] == []
