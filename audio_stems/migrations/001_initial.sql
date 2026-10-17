BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    input_type TEXT NOT NULL CHECK (input_type IN ('search_query', 'video_url')),
    input_value TEXT NOT NULL,
    source_id TEXT,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    submission_method TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    result_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_batches_owner_idempotency
    ON batches(owner_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    input_type TEXT NOT NULL CHECK (input_type IN ('search_query', 'video_url')),
    normalized_source TEXT NOT NULL,
    selected_source_id TEXT,
    status TEXT NOT NULL,
    current_stage TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_jobs_batch_id ON jobs(batch_id);
CREATE INDEX IF NOT EXISTS ix_jobs_owner_created ON jobs(owner_id, created_at);

CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('source_audio', 'stem')),
    stem_name TEXT,
    storage_key TEXT NOT NULL,
    media_format TEXT,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    checksum TEXT,
    model_name TEXT,
    model_version TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_media_assets_job_id ON media_assets(job_id);

CREATE TABLE IF NOT EXISTS job_events (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    actor_id TEXT REFERENCES clients(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_job_events_job_time ON job_events(job_id, occurred_at);

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

PRAGMA user_version = 1;
COMMIT;
