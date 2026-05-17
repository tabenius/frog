-- C3: read-only mirror of another workspace's event_log. Single-writer per
-- DB; cross-box visibility via event replay, not multi-writer to one file.
CREATE TABLE IF NOT EXISTS event_mirror (
    workspace TEXT NOT NULL,
    remote_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    repo_path TEXT,
    task_slug TEXT,
    actor TEXT,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    mirrored_at TEXT NOT NULL,
    PRIMARY KEY (workspace, remote_id)
);

CREATE INDEX IF NOT EXISTS idx_event_mirror_ws ON event_mirror(workspace, remote_id);
