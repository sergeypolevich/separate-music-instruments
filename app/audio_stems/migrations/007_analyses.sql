BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (7, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- On-demand analyses of a finished project (beats for the metronome); one row per project and kind.
CREATE TABLE analyses (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    progress REAL NOT NULL DEFAULT 0,
    request_json TEXT NOT NULL,
    result_json TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, kind)
);

PRAGMA user_version = 7;
COMMIT;
