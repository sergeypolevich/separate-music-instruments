ALTER TABLE jobs ADD COLUMN separation_options_json TEXT;

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

PRAGMA user_version = 2;
