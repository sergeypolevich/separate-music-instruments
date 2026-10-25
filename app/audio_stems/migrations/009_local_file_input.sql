-- A project can start from the user's own audio file (input_type 'local_file'). SQLite cannot change a CHECK
-- constraint, so projects and jobs are rebuilt. Foreign keys must be off (only possible outside a transaction):
-- with them on, dropping the old tables would cascade and delete every stem, event and analysis.
PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (9, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

CREATE TABLE projects_new (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    input_type TEXT NOT NULL CHECK (input_type IN ('search_query', 'video_url', 'local_file')),
    input_value TEXT NOT NULL,
    source_id TEXT,
    title TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO projects_new (id, owner_id, input_type, input_value, source_id, title, created_at, updated_at)
    SELECT id, owner_id, input_type, input_value, source_id, title, created_at, updated_at FROM projects;
DROP TABLE projects;
ALTER TABLE projects_new RENAME TO projects;

CREATE TABLE jobs_new (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    input_type TEXT NOT NULL CHECK (input_type IN ('search_query', 'video_url', 'local_file')),
    normalized_source TEXT NOT NULL,
    selected_source_id TEXT,
    status TEXT NOT NULL,
    current_stage TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    separation_options_json TEXT,
    progress REAL NOT NULL DEFAULT 0,
    start_seconds REAL,
    audio_seconds REAL,
    separation_started_at TEXT,
    separation_seconds REAL
);
INSERT INTO jobs_new (id, batch_id, project_id, owner_id, input_type, normalized_source, selected_source_id, status,
                      current_stage, retry_count, error_summary, created_at, updated_at, separation_options_json,
                      progress, start_seconds, audio_seconds, separation_started_at, separation_seconds)
    SELECT id, batch_id, project_id, owner_id, input_type, normalized_source, selected_source_id, status,
           current_stage, retry_count, error_summary, created_at, updated_at, separation_options_json,
           progress, start_seconds, audio_seconds, separation_started_at, separation_seconds FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX ix_jobs_batch_id ON jobs(batch_id);
CREATE INDEX ix_jobs_owner_created ON jobs(owner_id, created_at);

PRAGMA user_version = 9;
COMMIT;
PRAGMA foreign_keys = ON;
