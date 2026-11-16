-- LifeOps 002: uploaded documents, AI proposals and confirmed records (M2).

-- Inbox items can now be files, can fail with a reason, and remember their file.
CREATE TABLE inbox_items_new (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('text', 'file')),
  content TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('new', 'processing', 'needs_review', 'converted', 'archived', 'failed')),
  error TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO inbox_items_new (id, owner_id, kind, content, status, version, created_at, updated_at)
  SELECT id, owner_id, kind, content, status, version, created_at, updated_at FROM inbox_items;
DROP TABLE inbox_items;
ALTER TABLE inbox_items_new RENAME TO inbox_items;
CREATE INDEX inbox_owner_status ON inbox_items(owner_id, status);

-- An uploaded file: stored under a random name in data/files/, never under the user's file name.
CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  inbox_item_id INTEGER REFERENCES inbox_items(id) ON DELETE SET NULL,
  stored_name TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  media_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  pages INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE (owner_id, sha256)
);

-- What the extractor suggested (never a record until the user accepts it).
CREATE TABLE proposals (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  inbox_item_id INTEGER NOT NULL REFERENCES inbox_items(id) ON DELETE CASCADE,
  schema_version INTEGER NOT NULL,
  extractor TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'rejected', 'superseded')),
  fields TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX proposals_item ON proposals(owner_id, inbox_item_id, status);

-- Confirmed facts, as the user accepted (or corrected) them. Money as whole centavos/cents.
CREATE TABLE records (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  inbox_item_id INTEGER REFERENCES inbox_items(id) ON DELETE SET NULL,
  proposal_id INTEGER REFERENCES proposals(id) ON DELETE SET NULL,
  file_id INTEGER REFERENCES files(id) ON DELETE SET NULL,
  doc_type TEXT NOT NULL CHECK (doc_type IN ('bill', 'warranty', 'renewal', 'receipt', 'other')),
  title TEXT NOT NULL,
  supplier TEXT NOT NULL DEFAULT '',
  amount_cents INTEGER,
  currency TEXT,
  reference TEXT NOT NULL DEFAULT '',
  due_date TEXT,
  expiry_date TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX records_owner ON records(owner_id, created_at);

ALTER TABLE tasks ADD COLUMN source_record_id INTEGER REFERENCES records(id) ON DELETE SET NULL;
