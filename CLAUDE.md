# LifeOps — notes for Claude

Read this first when working here. Update it whenever the process, a decision or a rule changes (what changed goes in CHANGELOG.md). This repository is public: keep personal details out of it (they belong in the git-ignored `CLAUDE.local.md`).

## What it is
- A personal life-admin assistant: capture → (AI) proposals with evidence → review → records, tasks, reminders.
- Single-user, self-hosted. Python FastAPI + SQLite + plain HTML/CSS/JS.
- Features: login (username, optional two-step login), Inbox (notes and uploads), document readers (built-in, local Ollama, Claude), review, records, tasks, Today dashboard, activity history, export and delete, Home Screen app with push reminders, backups.

## Design rules (don't undo without a reason)
- Clean minimal + warm (Inter + JetBrains Mono, green accent), follows the device with a toggle, plain background. Mobile first: bottom tabs on phones, top tabs ≥ 760px.
- Plain rows with hairlines (no boxed cards), quiet text actions, category as grey text (chips only for status), Settings as `.set` sections, Log out always in the top bar.
- Rows: every action in one ⋯ menu on the right (`moreMenu`, items are buttons or `{href}` links); Inbox rows keep only their one main action visible; filters are text tabs (`.seg`).
- Dropdowns use `select.input` with the `--select-arrow` chevron (never the `background` shorthand on `.input`: it wipes out the arrow).
- Settings save on change, one field per PATCH (no Save buttons).
- Today is a dashboard: `/api/today` adds `bills_due`, `bills_total` per currency and `expiring` (`dashboard()` in routers/tasks.py); a bill counts as paid when a task with its `source_record_id` is done.
- `.view` columns are `minmax(0, 1fr)` so a long `<select>` can't widen the page.
- The top bar must stay solid: a `backdrop-filter` on it traps the fixed bottom tabs inside it.

## Accounts and security
- Login name is a **username**: `^[A-Za-z0-9._-]{3,32}$`, case-insensitive. No email is collected.
- Sessions in the DB (not signed cookies). No public sign-up: the first account is created on the page (`/api/setup`), only while zero accounts exist AND both the client address and the Host header are local (127.0.0.1/::1/localhost, so a reverse proxy can't open it); later accounts or password resets via `python -m app.manage`.
- Two-step login: TOTP in `app/totp.py` (standard library only), migration 006. Login returns `{two_step, ticket}` and `/api/login/code` finishes it; `api()` in app.js must not treat a 401 from `/api/login` or `/api/login/code` as an expired session. The setup QR code is drawn in the browser (`web/assets/js/qrcode.js`): never send the secret to a QR service. Lockout fix: `python -m app.manage disable-2fa <username>`.

## Stack and structure
- `app/` (main, settings, db, security, models, services, routers/, manage), `migrations/NNN_*.sql`, `web/`, `tests/`.
- Run: `.venv/bin/uvicorn --factory app.main:create_app --port 8780` (factory: importing has no side effects).
- Data: `data/` (git-ignored). Dates: instants UTC ISO "…Z"; due dates `YYYY-MM-DD`; "today" uses the account's IANA timezone.
- Rules: parameterized SQL with `owner_id = ?` always; `transaction()` for multi-row writes; Idempotency-Key on creates; `version` on edits (409 when stale); activity log never stores content; textContent only in the page.
- Uploads: max 10 MB, PDF/JPEG/PNG/HEIC by signature; random names in `data/files/`; JPEG/PNG metadata stripped; duplicates (same sha256) refused.
- Extractor contract = schema v1 in `app/extractor.py`; every proposal goes through `validate_proposal`; unknown → null; ambiguous dates offer both options. Records/tasks only on accept (one transaction).
- Migrations run with foreign keys off and `foreign_key_check` after each file (table rebuilds). Test any new migration on older-shaped data (see `test_migration_002_keeps_m1_data`).
- Workflow runs (`app/workflow.py`): change state only through `move()` (checks TRANSITIONS, records the step, bumps version, audits with the correlation id). Retry resumes the failed run (attempts + 1).
- Delete: password + "DELETE"; scope data = per-table deletes (OWNED_TABLES), scope account = delete the user (cascades). Remove files after the commit. New owned tables must be added to OWNED_TABLES and EXPORT_TABLES.
- Reminders: one per open dated task (`reminders.sync_task` after EVERY task change); scheduler thread (LIFEOPS_SCHEDULER_SECONDS, 0 in tests); neutral push text only. Push endpoints must match ALLOWED_PUSH_HOSTS (SSRF guard). VAPID key: env or `data/vapid_private.pem` (0600).
- Claude reader (`app/claude_reader.py`): model `claude-opus-5-5` via `client.beta.messages.create` with structured output (json_schema), `fallbacks="default"` + beta `server-side-fallback-2026-07-01`, no tools, document between markers; result goes through `validate_proposal`. Used only if chosen AND `ANTHROPIC_API_KEY` is set. Tests use a fake client: never call the real API in tests (costs money).
- Local reader (`app/local_reader.py`): Ollama `/api/chat` with `format` = the Claude reader's OUTPUT_SCHEMA and its SYSTEM_PROMPT, temperature 0, no tools, document between markers; photos as `images`, PDFs as page text (scans without text refused). URL locked to http + 127.0.0.1/localhost/::1 (SSRF guard). Model: LIFEOPS_OLLAMA_MODEL or the first installed. Standard library only (urllib). Tests set LIFEOPS_OLLAMA_URL to an unusable address in conftest and fake `local_reader.call`: never reach a real Ollama in tests. Choosing `local` while Ollama is closed: PATCH 409; uploads fail with a retryable reason (no silent fallback to another reader).
- Reading a document = 3 steps in `routers/documents.py`: a short transaction (`begin_reading` / `workflow.start`), `read_document()` with NO transaction open (upload: on a worker thread via `run_in_threadpool`, the route is async), then a short transaction `save_result()` (skips if the run was stopped meanwhile). Never read inside `transaction()`: a slow reader would lock SQLite for every other change. At start, `workflow.fail_interrupted` fails runs left queued/running (assumes ONE server process).
- Service worker caches the shell only; never cache `/api/`. Bump CACHE in `web/sw.js` when the shell list changes.
- Page files are served with `Cache-Control: no-cache` (browsers kept stale JS before).
- SQLite connections use `check_same_thread=False` (opened in middleware, used on FastAPI's worker thread; one connection per request).

## Platforms
- Runs on Windows, macOS and Linux (Python 3.12+). `start.bat` / `start.command` create `.venv` on first run, install `requirements.lock`, start uvicorn on 127.0.0.1:8780 and open the browser. `.gitattributes` keeps `.bat` CRLF and `.command` LF; `start.command` must stay executable.
- GitHub Actions (`.github/workflows/tests.yml`): tests on windows/macos/ubuntu (3.14) + ubuntu 3.12, then a real server start. Actions pinned to commit SHAs; `permissions: contents: read`. Keep all three green.
- Platform-specific bits: HEIC conversion uses macOS `sips` (`documents.SIPS`); elsewhere `NO_HEIC_HERE` tells users to upload JPEG. File-permission checks in tests are skipped on Windows (no Unix permission bits). Use "this computer", never "this Mac", in the UI.

## Run, test
- Tests: `.venv/bin/python -m pytest -q -p no:cacheprovider` (then delete `__pycache__/`). Web lint: `npm run lint` in a temporary copy with the pinned tools, then delete the copy.
- Browser tests: a throwaway data dir (`LIFEOPS_DATA_DIR`) and a throwaway account; delete both afterwards. Never test on `data/`.
- Never commit `data/`, `backups/`, `.env`, `CLAUDE.local.md` or any personal document.

## Open items
- HEIC photos are stored as uploaded (metadata kept on disk, private); for Claude they are converted with `sips` (macOS) and stripped first. Converting at upload time would need a cross-platform library when hosted.
- Push was verified with a fake sender and API tests; a real end-to-end push is still to be tried.
- Recovery codes have no Copy/Download button yet (shown once on screen).
- Before going online: HTTPS + `LIFEOPS_COOKIE_SECURE=1`, hosted backups (the local backup/restore exists), VAPID key in env, a scheduler that runs where it's hosted, and recheck the privacy notice.
