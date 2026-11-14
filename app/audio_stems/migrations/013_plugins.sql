BEGIN IMMEDIATE;

-- Claims this version first, under the write lock: a second process applying it fails here, before any change.
INSERT INTO schema_migrations(version, applied_at)
VALUES (13, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));

-- Installed plugins (version 22): on or off for every project; a plugin that broke is off with its error.
CREATE TABLE plugins (
    id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    error TEXT,
    installed_at TEXT NOT NULL
);

-- What a plugin keeps for one project (trim bounds, EQ bands, edited chords); gone with the plugin or the project.
CREATE TABLE plugin_data (
    plugin_id TEXT NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (plugin_id, project_id)
);

PRAGMA user_version = 13;
COMMIT;
