"""M2: uploads, extraction, review, records, file downloads and the 002 migration."""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app.db import MIGRATIONS, connect, migrate, utc_now
from app.documents import MAX_FILE_BYTES, strip_jpeg_metadata, strip_png_metadata
from app.extractor import MockExtractor, validate_proposal


# ---- test files --------------------------------------------------------------------------------
def pdf_with_text(*pages: str) -> bytes:
    """A small, valid PDF with one line of text per page (built by hand, with a correct xref table)."""
    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font_id = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content_id = 4 + 2 * i
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        )
        lines = text.split("\n")
        ops = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({ln}) Tj T*" for ln in lines) + " ET"
        objects.append(f"<< /Length {len(ops)} >>\nstream\n{ops}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


JPEG_WITH_EXIF = (
    b"\xff\xd8"  # start
    + b"\xff\xe1\x00\x10Exif\x00\x00GPS-SECRET"  # APP1 (EXIF) with fake GPS: must be removed
    + b"\xff\xe0\x00\x06JFIF"  # APP0: kept
    + b"\xff\xda\x00\x02image-data\xff\xd9"  # start of scan + data + end
)
PNG_WITH_TEXT = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 13 + b"\x00\x00\x00\x00"
    + b"\x00\x00\x00\x0ftEXtLocation\x00Home" + b"\x00\x00\x00\x00"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)  # fmt: skip

BILL = pdf_with_text(
    "Meralco Electric Company\nStatement of Account\nAccount No. 1234-5678\nAmount due PHP 2,345.60\nPlease pay by 12 March 2027"
)  # one page, five lines


def upload(user, name: str, data: bytes, content_type: str = "application/octet-stream"):
    return user.client.post("/api/inbox/upload", files={"file": (name, data, content_type)}, headers=user.headers())


# ---- extractor ---------------------------------------------------------------------------------
def test_mock_extractor_finds_facts_with_evidence():
    fields = MockExtractor().extract(["Meralco Electric bill\nAccount No. 1234-5678\nAmount due PHP 2,345.60\nPay by 12 March 2027"])["fields"]
    assert fields["doc_type"]["value"] == "bill"
    assert fields["amount"]["value"] == "2345.60" and fields["currency"]["value"] == "PHP"
    assert fields["reference"]["value"] == "1234-5678"
    assert fields["due_date"]["value"] == "2027-03-12"
    assert fields["due_date"]["date_kind"] == "explicit"
    assert "12 March 2027" in fields["due_date"]["evidence"]["snippet"]
    assert fields["due_date"]["evidence"]["page"] == 1


def test_reference_is_never_a_plain_word_and_evidence_keeps_the_whole_value():
    text = "Meralco Electric Company Statement of Account Account No. 1234-5678 Amount due PHP 2,345.60 Please pay by 12 March 2027"
    fields = MockExtractor().extract([text])["fields"]
    assert fields["reference"]["value"] == "1234-5678"
    assert "2,345.60" in fields["amount"]["evidence"]["snippet"]
    assert "12 March 2027" in fields["due_date"]["evidence"]["snippet"]


def test_ambiguous_dates_are_not_guessed():
    fields = MockExtractor().extract(["Warranty valid until 03/04/2027"])["fields"]
    expiry = fields["expiry_date"]
    assert expiry["value"] is None
    assert expiry["date_kind"] == "ambiguous"
    assert expiry["options"] == ["2027-03-04", "2027-04-03"]


def test_unknowns_stay_empty():
    fields = MockExtractor().extract([""])["fields"]
    assert all(f["value"] is None for f in fields.values())


def test_proposal_validation_drops_bad_values():
    bad = {
        "schema": 1,
        "fields": {
            "doc_type": {"value": "spaceship", "confidence": "high"},
            "due_date": {"value": "next Tuesday", "confidence": "high"},
            "amount": {"value": "lots", "confidence": "certain"},
            "surprise": {"value": "ignored"},
        },
    }
    clean = validate_proposal(bad)["fields"]
    assert clean["doc_type"]["value"] is None
    assert clean["due_date"]["value"] is None
    assert clean["amount"]["value"] is None and clean["amount"]["confidence"] == "low"
    assert "surprise" not in clean
    with pytest.raises(ValueError):
        validate_proposal({"schema": 99})


def test_photo_metadata_is_removed():
    cleaned = strip_jpeg_metadata(JPEG_WITH_EXIF)
    assert b"GPS-SECRET" not in cleaned and b"JFIF" in cleaned and cleaned.endswith(b"\xff\xd9")
    cleaned_png = strip_png_metadata(PNG_WITH_TEXT)
    assert b"Location" not in cleaned_png and cleaned_png.endswith(b"IEND\xaeB`\x82")


# ---- uploads ----------------------------------------------------------------------------------
def test_pdf_upload_is_read_and_waits_for_review(me, app):
    response = upload(me, "bill.pdf", BILL, "application/pdf")
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["status"] == "needs_review" and item["kind"] == "file"
    assert item["file"]["name"] == "bill.pdf" and item["file"]["pages"] == 1
    review = me.get(f"/api/inbox/{item['id']}/review").json()
    fields = review["proposal"]["fields"]
    assert fields["due_date"]["value"] == "2027-03-12"
    assert fields["amount"]["value"] == "2345.60"
    stored = list((app.state.settings.data_dir / "files").iterdir())
    assert len(stored) == 1 and stored[0].name != "bill.pdf"  # random name, never the user's


def test_renamed_and_unknown_files_are_refused(me):
    assert upload(me, "invoice.pdf", b"MZ\x90\x00 this is really a program").status_code == 415
    assert upload(me, "page.html", b"<html><script>alert(1)</script>").status_code == 415
    assert upload(me, "logo.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>").status_code == 415
    assert upload(me, "empty.pdf", b"").status_code == 422


def test_too_big_and_damaged_files(me):
    big = b"%PDF-1.4\n" + b"0" * (MAX_FILE_BYTES + 10)
    assert upload(me, "big.pdf", big).status_code == 413
    damaged = upload(me, "broken.pdf", b"%PDF-1.4\nnot really a pdf")
    assert damaged.status_code == 422
    assert "couldn't be read" in damaged.json()["detail"]


def test_same_file_twice_is_refused(me):
    assert upload(me, "bill.pdf", BILL).status_code == 201
    assert upload(me, "copy.pdf", BILL).status_code == 409


def test_photo_upload_starts_an_empty_review(me):
    item = upload(me, "warranty.jpg", JPEG_WITH_EXIF, "image/jpeg").json()
    review = me.get(f"/api/inbox/{item['id']}/review").json()
    assert review["proposal"]["note"].startswith("No text could be read")
    assert all(f["value"] is None for f in review["proposal"]["fields"].values())
    download = me.get(f"/api/files/{review['file']['id']}?inline=1")
    assert download.headers["content-disposition"].startswith("inline")
    assert b"GPS-SECRET" not in download.content


# ---- review: accept / reject -------------------------------------------------------------------
def test_accept_makes_a_record_and_a_task_together(me):
    item = upload(me, "bill.pdf", BILL).json()
    body = {
        "version": item["version"],
        "doc_type": "bill",
        "title": "Electricity bill",
        "supplier": "Meralco",
        "amount": "2,345.60",
        "currency": "PHP",
        "reference": "1234-5678",
        "due_date": "2027-03-12",
        "make_task": True,
        "task_title": "Pay electricity bill",
        "task_due_date": "2027-03-10",
        "task_category": "bills",
    }
    result = me.post(f"/api/inbox/{item['id']}/accept", json=body)
    assert result.status_code == 201, result.text
    record, task = result.json()["record"], result.json()["task"]
    assert record["amount"] == "2345.60" and record["file"]["name"] == "bill.pdf"
    assert task["source_record_id"] == record["id"] and task["due_date"] == "2027-03-10"
    assert me.get("/api/records").json()["records"][0]["id"] == record["id"]
    assert me.get("/api/inbox?show=converted").json()["items"][0]["id"] == item["id"]
    again = me.post(f"/api/inbox/{item['id']}/accept", json=body)
    assert again.status_code == 409


def test_accept_rejects_bad_input(me):
    item = upload(me, "bill.pdf", BILL).json()
    base = {"version": item["version"], "doc_type": "bill", "title": "x"}
    assert me.post(f"/api/inbox/{item['id']}/accept", json={**base, "amount": "lots"}).status_code == 422
    assert me.post(f"/api/inbox/{item['id']}/accept", json={**base, "make_task": True}).status_code == 422
    assert me.post(f"/api/inbox/{item['id']}/accept", json={**base, "doc_type": "spaceship"}).status_code == 422


def test_reject_keeps_history_and_returns_the_note(me):
    note = me.post("/api/inbox", json={"content": "Car registration expires 5 June 2027"}).json()
    suggested = me.post(f"/api/inbox/{note['id']}/suggest", json={"version": note["version"]}).json()
    assert suggested["status"] == "needs_review"
    review = me.get(f"/api/inbox/{note['id']}/review").json()
    assert review["proposal"]["fields"]["expiry_date"]["value"] == "2027-06-05"
    rejected = me.post(f"/api/inbox/{note['id']}/reject", json={"version": suggested["version"]}).json()
    assert rejected["status"] == "new"
    assert me.get(f"/api/inbox/{note['id']}/review").json()["proposal"] is None


def test_another_users_documents_look_missing(me, other):
    item = upload(me, "bill.pdf", BILL).json()
    assert other.get(f"/api/inbox/{item['id']}/review").status_code == 404
    assert other.get(f"/api/files/{item['file']['id']}").status_code == 404
    accept = other.post(f"/api/inbox/{item['id']}/accept", json={"version": item["version"], "doc_type": "bill", "title": "x"})
    assert accept.status_code == 404
    assert other.get("/api/records").json()["records"] == []


def test_downloads_are_attachments_with_nosniff(me):
    item = upload(me, "bill.pdf", BILL).json()
    response = me.get(f"/api/files/{item['file']['id']}?inline=1")  # PDFs are never shown inline
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-type"] == "application/pdf"


def test_deleting_an_item_deletes_its_file(me, app):
    item = upload(me, "bill.pdf", BILL).json()
    folder = app.state.settings.data_dir / "files"
    assert len(list(folder.iterdir())) == 1
    assert me.delete(f"/api/inbox/{item['id']}").status_code == 204
    assert list(folder.iterdir()) == []


# ---- migration -------------------------------------------------------------------------------------
def test_migration_002_keeps_m1_data(tmp_path: Path):
    only_001 = tmp_path / "m1"
    only_001.mkdir()
    shutil.copy(MIGRATIONS / "001_init.sql", only_001 / "001_init.sql")
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    with closing(conn):
        migrate(conn, only_001)
        now = utc_now()
        conn.execute("INSERT INTO users (email, password_hash, timezone, created_at) VALUES ('a@b.c', 'x', 'Asia/Manila', ?)", (now,))  # M1 had email
        conn.execute("INSERT INTO inbox_items (owner_id, kind, content, status, created_at, updated_at) VALUES (1, 'text', 'note', 'converted', ?, ?)", (now, now))
        conn.execute("INSERT INTO tasks (owner_id, title, status, source_inbox_id, created_at, updated_at) VALUES (1, 'task', 'open', 1, ?, ?)", (now, now))
        applied = migrate(conn)
        assert applied[0] == "002_documents.sql" and "003_workflow_runs.sql" in applied
        assert conn.execute("SELECT content, error FROM inbox_items").fetchone()[:] == ("note", "")
        assert conn.execute("SELECT source_inbox_id, source_record_id FROM tasks").fetchone()[:] == (1, None)
        assert "005_username.sql" in applied
        assert conn.execute("SELECT username FROM users").fetchone()[0] == "a@b.c"  # renamed, kept
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
