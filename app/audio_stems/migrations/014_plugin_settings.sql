BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (14, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- A plugin's settings (version 23), the same in every project (the chord names' size); gone with the plugin.
ALTER TABLE plugins ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}';

PRAGMA user_version = 14;
COMMIT;
