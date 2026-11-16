-- LifeOps 001: accounts, sessions, inbox, tasks, activity, duplicate protection (M1).
-- Instants are UTC ISO 8601 text ("...Z"); date-only values (due dates) are 'YYYY-MM-DD' text.

CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  timezone TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Only a SHA-256 of the session token is stored, so a copy of the database can't be used to log in.
CREATE TABLE sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX sessions_user ON sessions(user_id);

CREATE TABLE inbox_items (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('text')),
  content TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('new', 'processing', 'needs_review', 'converted', 'archived', 'failed')),
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX inbox_owner_status ON inbox_items(owner_id, status);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  details TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'other',
  due_date TEXT,
  snoozed_until TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'done', 'cancelled')),
  source_inbox_id INTEGER REFERENCES inbox_items(id) ON DELETE SET NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX tasks_owner_status_due ON tasks(owner_id, status, due_date);

-- Activity: what happened, never the content itself (no personal details in the log).
CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  at TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX audit_owner_at ON audit_events(owner_id, at);

-- Duplicate protection: the same Idempotency-Key from the same owner returns the first result.
CREATE TABLE idempotency_keys (
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  route TEXT NOT NULL,
  response TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (owner_id, key)
);
