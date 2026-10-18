BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

ALTER TABLE jobs ADD COLUMN separation_options_json TEXT;

PRAGMA user_version = 2;
COMMIT;
