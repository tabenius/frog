CREATE TABLE IF NOT EXISTS units (
    unit_path TEXT PRIMARY KEY,
    repo_path TEXT NOT NULL,
    name TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    kind TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    discovery_source TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(repo_path, rel_path),
    FOREIGN KEY (repo_path) REFERENCES repos(repo_path) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_units_repo_path ON units(repo_path);
CREATE INDEX IF NOT EXISTS idx_units_kind ON units(repo_path, kind);
