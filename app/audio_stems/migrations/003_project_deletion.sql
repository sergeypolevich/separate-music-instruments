BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS pending_project_deletions (
    project_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    storage_keys_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_pending_project_deletions_owner
    ON pending_project_deletions(owner_id);

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (3, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

PRAGMA user_version = 3;
COMMIT;
