BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (11, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- The name the user gave the project; the source title stays, it names the files on disk.
ALTER TABLE projects ADD COLUMN name TEXT;

PRAGMA user_version = 11;
COMMIT;
