-- LifeOps 005: log in with a username instead of an email.
-- The column is renamed in place; its UNIQUE COLLATE NOCASE rule (case doesn't matter) is kept.
ALTER TABLE users RENAME COLUMN email TO username;
