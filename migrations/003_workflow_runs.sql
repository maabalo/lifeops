-- LifeOps 003: workflow runs (M3). Every reading/extraction of an Inbox item is a run with an explicit
-- state, its steps, how many attempts it took, and a correlation id that ties its activity together.
CREATE TABLE workflow_runs (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  inbox_item_id INTEGER NOT NULL REFERENCES inbox_items(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('extract')),
  state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'needs_review', 'completed', 'failed', 'cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0,
  correlation_id TEXT NOT NULL,
  steps TEXT NOT NULL DEFAULT '[]',
  error TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX workflow_runs_item ON workflow_runs(owner_id, inbox_item_id, id);
