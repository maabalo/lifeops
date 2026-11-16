-- LifeOps 007: a third document reader, 'local' (an Ollama model on this computer).
-- SQLite can't change a CHECK rule in place, so the users table is rebuilt (foreign keys are off while
-- migrations run, and checked afterwards). Every column and value is kept.

CREATE TABLE users_new (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  timezone TEXT NOT NULL,
  created_at TEXT NOT NULL,
  reminder_time TEXT NOT NULL DEFAULT '08:00',
  extractor TEXT NOT NULL DEFAULT 'mock' CHECK (extractor IN ('mock', 'claude', 'local')),
  totp_secret TEXT,
  totp_pending TEXT,
  totp_last_step INTEGER NOT NULL DEFAULT 0
);

INSERT INTO users_new (id, username, password_hash, timezone, created_at, reminder_time, extractor,
                       totp_secret, totp_pending, totp_last_step)
SELECT id, username, password_hash, timezone, created_at, reminder_time, extractor,
       totp_secret, totp_pending, totp_last_step
FROM users;

DROP TABLE users;

ALTER TABLE users_new RENAME TO users;
