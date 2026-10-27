BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (10, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- The user asked to stop this work; the worker checks it while it downloads or separates.
ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
ALTER TABLE analyses ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;

PRAGMA user_version = 10;
COMMIT;
