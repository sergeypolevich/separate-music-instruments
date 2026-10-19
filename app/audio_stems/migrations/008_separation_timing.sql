BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (8, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- How long the song is and how long its separation took: the basis of the time estimates.
ALTER TABLE jobs ADD COLUMN audio_seconds REAL;
ALTER TABLE jobs ADD COLUMN separation_started_at TEXT;
ALTER TABLE jobs ADD COLUMN separation_seconds REAL;

PRAGMA user_version = 8;
COMMIT;
