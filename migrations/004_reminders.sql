-- LifeOps 004: reminders and push notifications (M4), and the reader choice (M5).

ALTER TABLE users ADD COLUMN reminder_time TEXT NOT NULL DEFAULT '08:00';
ALTER TABLE users ADD COLUMN extractor TEXT NOT NULL DEFAULT 'mock' CHECK (extractor IN ('mock', 'claude'));

-- One reminder per task due date. dedup_key stops the same reminder being made (or sent) twice.
CREATE TABLE reminders (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  remind_at TEXT NOT NULL,
  timezone TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'cancelled')),
  dedup_key TEXT NOT NULL,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  UNIQUE (owner_id, dedup_key)
);
CREATE INDEX reminders_due ON reminders(state, remind_at);

-- Browsers/devices that agreed to receive push notifications.
CREATE TABLE push_subscriptions (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  endpoint TEXT NOT NULL UNIQUE,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_ok_at TEXT
);
CREATE INDEX push_owner ON push_subscriptions(owner_id);
