# LifeOps

A personal life-admin assistant: jot down or upload bills, renewals and warranties, check what it found, turn
them into records and tasks with due dates, and see what's overdue, due soon and done. Python FastAPI + SQLite +
plain HTML/CSS/JS, for one user (you), running on your own computer.

**Features:** login with a username and optional two-step login (Google Authenticator) · Inbox notes and uploads
(PDF/JPEG/PNG/HEIC, 10 MB) · suggestions with evidence from a document reader: built-in, a local model (Ollama,
free and private) or Claude (optional) · Review screen · Records · tasks · Today dashboard (numbers, bills due,
expiring soon) · Activity history · export everything (ZIP), delete all data / delete account · Home Screen app,
push reminders, backups, housekeeping, privacy notice.
**Not yet:** hosting online (HTTPS, hosted backups).

## Run it (Windows, macOS or Linux)

You need **Python 3.12 or newer** (free: https://www.python.org/downloads/; on Windows tick "Add python.exe to PATH").
Download LifeOps (green **Code** button → **Download ZIP**, then unzip it), then:

| | Start LifeOps |
|---|---|
| **Windows** | Double-click **`start.bat`** |
| **macOS** | Double-click **`start.command`** (the first time: right-click → Open, because it was downloaded) |
| **Linux** | `sh start.command` |

The first start sets everything up (about a minute); after that it starts in seconds and opens
http://127.0.0.1:8780 in your browser. The first time, it asks you to **create your account** (this works only on the
computer running LifeOps, and only while there is no account). After that, log in. Close the window (or press Ctrl+C)
to stop LifeOps.

Your data lives in `data/` (the database `lifeops.sqlite3`); it is git-ignored and never uploaded.

<details><summary>By hand, without the start scripts</summary>

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock            # Windows: .venv\Scripts\pip install -r requirements.lock
.venv/bin/uvicorn --factory app.main:create_app --port 8780   # Windows: .venv\Scripts\uvicorn ...
```
</details>

Other account commands (in Terminal, or Command Prompt on Windows, inside the LifeOps folder; Windows: use
`.venv\Scripts\python` instead of `python`): `python -m app.manage set-password <username>`,
`python -m app.manage logout-everywhere <username>`, `python -m app.manage disable-2fa <username>` (turns two-step
login off if you lose your phone and recovery codes).

**Windows note:** iPhone HEIC photos can't be read there (macOS converts them with a built-in tool). Upload JPEG
instead: on iPhone, Settings → Camera → Formats → Most Compatible. PDFs, JPEG and PNG work everywhere.

## A local model as the reader (optional, free)

1. Install Ollama (https://ollama.com, Windows, macOS and Linux), open it, then download a model once in Terminal or Command Prompt: `ollama pull gemma3:12b` (about 8 GB; it reads
   PDFs and photos). A smaller one: `ollama pull qwen2.5vl:7b`.
2. In LifeOps: Settings → Document reader → **On this computer (Ollama)** (it saves straight away).

Nothing leaves your computer. Keep Ollama open while uploading; if it's closed, the upload stops with Try again.
Scanned PDFs without text can't be read by the local model (photos can). `LIFEOPS_OLLAMA_MODEL` picks the model.

## Settings (environment variables)

See `.env.example`. `LIFEOPS_DATA_DIR` (where data lives), `LIFEOPS_COOKIE_SECURE` (`1` once served over
HTTPS), `LIFEOPS_SESSION_DAYS`, `LIFEOPS_TIMEZONE` (default for new accounts). No secrets are needed yet.

## Security (M1)

- Passwords hashed with Argon2id; login rate-limited (5 tries per 15 minutes); same answer for unknown
  username and wrong password.
- Optional two-step login (TOTP, RFC 6238: works with Google Authenticator): ±30 s drift, a code is never
  accepted twice, 10 one-time recovery codes stored as SHA-256, a 5-minute login ticket with at most 5 tries.
- Sessions: random token in an HttpOnly, SameSite=Strict cookie; only its SHA-256 is stored; they expire.
- Every change needs the session's CSRF token, and a same-origin `Origin`.
- Every query includes the owner (`owner_id = ?`); another user's item looks "not found" (404). Parameterized SQL only.
- Strict Content-Security-Policy (no inline scripts), no caching of personal data, 64 KB request limit,
  validation errors that never echo what you typed, an activity log that never stores content.
- Duplicate protection: new items carry an `Idempotency-Key`; a double tap or retry creates one item.
- Edits carry a `version`; a stale edit from another window is refused (409) instead of overwriting.
- Uploads (M2): allowlist by file signature (PDF/JPEG/PNG/HEIC), 10 MB, random stored names in `data/files/`, photo
  metadata removed, downloads as attachments with `nosniff` (only JPEG/PNG may show inline in the review),
  owner-checked every time. Document text is treated as data; suggestions are never saved until you accept them.
- Delete needs your password (rate-limited) and the word DELETE; files are removed from disk after the database
  commit. Export is made on request and streamed; CSV cells starting with = + - @ are neutralised.

## Project layout

| Path | What it is |
|---|---|
| `app/main.py` | App factory, security headers, size limit, one DB connection per request |
| `app/settings.py` | Environment settings |
| `app/db.py` | SQLite (WAL, foreign keys), transactions, migrations runner |
| `app/security.py` | Passwords, sessions, CSRF, login rate limit |
| `app/models.py` | Request models (sizes and formats checked here) |
| `app/services.py` | Activity log, idempotency, ownership and version checks, task status rules |
| `app/routers/` | `auth.py`, `inbox.py`, `tasks.py` (also the Today view), `documents.py` (upload, review, records, files) |
| `app/documents.py` | Upload checks (file signatures, size), photo metadata removal, private storage, PDF text |
| `app/extractor.py` | The extractor contract (schema v1), the mock extractor, and validation of any proposal |
| `app/workflow.py` | Workflow runs: the allowed state changes, steps, attempts, correlation ids |
| `app/routers/account.py` | Activity history, export (ZIP), delete data / delete account |
| `app/reminders.py` | Reminders that follow tasks, the scheduler, housekeeping (retention) |
| `app/push.py` | Web Push: VAPID key, allowed push services, sending |
| `app/backup.py` | Backup and restore (with integrity check) |
| `app/claude_reader.py` | Claude as the reader: request, strict output schema, plain-words errors |
| `app/local_reader.py` | A local model (Ollama on this computer) as the reader: same form and checks, this computer only |
| `app/manage.py` | Account, backup and restore commands (no public sign-up) |
| `migrations/` | Numbered SQL files, applied in order on start |
| `web/` | The page: `index.html`, `assets/css/style.css`, `assets/js/app.js` |
| `tests/` | pytest: auth, CSRF, ownership, idempotency, state changes, versions, Today grouping |

## Tests and checks

```sh
.venv/bin/python -m pytest -q -p no:cacheprovider   # backend tests (temporary database per test)
npm run lint                                        # web checks (needs `npm install`; Node only for this)
```
