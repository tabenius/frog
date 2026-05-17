CREATE TABLE IF NOT EXISTS repo_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    workdir TEXT NOT NULL,
    runner TEXT,
    source TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    aggregate INTEGER NOT NULL DEFAULT 0,
    needs_lock INTEGER NOT NULL DEFAULT 0,
    lock_kind TEXT,
    destructive INTEGER NOT NULL DEFAULT 0,
    network_required INTEGER NOT NULL DEFAULT 0,
    artifact_paths_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(repo_path, target_kind, name, workdir, command)
);

CREATE INDEX IF NOT EXISTS idx_repo_targets_repo_path ON repo_targets(repo_path);
CREATE INDEX IF NOT EXISTS idx_repo_targets_kind ON repo_targets(repo_path, target_kind);

CREATE TABLE IF NOT EXISTS repo_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    artifact_name TEXT NOT NULL,
    path_hint TEXT NOT NULL,
    source TEXT,
    target_kind TEXT,
    target_name TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(repo_path, artifact_name, path_hint)
);

CREATE INDEX IF NOT EXISTS idx_repo_artifacts_repo_path ON repo_artifacts(repo_path);

CREATE TABLE IF NOT EXISTS repo_detection_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_path TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_repo_detection_sources_repo_path
ON repo_detection_sources(repo_path);
