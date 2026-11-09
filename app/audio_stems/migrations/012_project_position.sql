BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (12, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- The order the user dragged the projects into; NULL (a new project) comes after them, by creation time.
ALTER TABLE projects ADD COLUMN position INTEGER;

PRAGMA user_version = 12;
COMMIT;
