BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (5, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- Share of the job's work done, 0..1; shown as the fill of the project tab.
ALTER TABLE jobs ADD COLUMN progress REAL NOT NULL DEFAULT 0;

PRAGMA user_version = 5;
COMMIT;
