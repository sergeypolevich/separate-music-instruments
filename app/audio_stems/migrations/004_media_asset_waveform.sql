BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (4, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- Cached waveform peaks for stem assets (JSON), computed once from the WAV on first request.
ALTER TABLE media_assets ADD COLUMN waveform_json TEXT;

PRAGMA user_version = 4;
COMMIT;
