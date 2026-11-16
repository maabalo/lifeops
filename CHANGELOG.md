# Changelog

Updates to this project, newest first.

## 1.0.0 — first public version
### Added
- Login with a username; the first account is created on the page (only on the computer running LifeOps). Optional two-step login with an authenticator app and one-time recovery codes.
- Inbox: jot notes, upload bills and documents (PDF, JPEG, PNG, HEIC up to 10 MB; checked by file signature; photo metadata removed).
- Document readers: a built-in reader, a local model through Ollama (free; nothing leaves the computer) or Claude (optional, needs an API key). Documents are treated as data, never instructions; every answer is checked before you see it.
- Review screen with evidence for each suggestion; records and tasks are made only when you accept.
- Tasks with due dates, snooze and categories; Today dashboard with overdue, due this week, Inbox count, bills due (30 days, with totals) and things expiring (90 days).
- Workflow runs with retries; Activity history that never stores content.
- Export everything (ZIP with JSON, CSV and your files); delete all data or the whole account.
- Home Screen app (PWA), push reminders with neutral text, a scheduler, backups and restore, housekeeping, privacy notice.
- 92 backend tests (Claude and Ollama are faked: tests never call a real model).
