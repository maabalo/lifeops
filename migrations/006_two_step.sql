-- LifeOps 006: two-step login with an authenticator app (TOTP, RFC 6238) and recovery codes.

ALTER TABLE users ADD COLUMN totp_secret TEXT;            -- set once two-step login is on
ALTER TABLE users ADD COLUMN totp_pending TEXT;           -- during setup, until the first code is confirmed
ALTER TABLE users ADD COLUMN totp_last_step INTEGER NOT NULL DEFAULT 0;  -- a code can't be used twice

-- One-time recovery codes: only a SHA-256 of each is stored.
CREATE TABLE recovery_codes (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX recovery_owner ON recovery_codes(owner_id);

-- Between the password and the code: a short-lived ticket (only its SHA-256 is stored).
CREATE TABLE login_tickets (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0
);
