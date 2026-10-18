BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (6, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- Where the music starts in the stems (leading silence skipped); NULL until measured.
ALTER TABLE jobs ADD COLUMN start_seconds REAL;

PRAGMA user_version = 6;
COMMIT;
