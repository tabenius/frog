-- B1: per-target run cache. A successful run with an unchanged input
-- fingerprint lets `frog repo build` skip the target instead of re-running.
CREATE TABLE IF NOT EXISTS target_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_path TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_name TEXT NOT NULL,
    workdir TEXT NOT NULL,
    command TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    returncode INTEGER NOT NULL,
    status TEXT NOT NULL,            -- 'ran' | 'cached'
    duration_ms INTEGER,
    ran_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_target_runs_lookup
ON target_runs(repo_path, target_kind, target_name, workdir, command, input_hash);

CREATE INDEX IF NOT EXISTS idx_target_runs_repo ON target_runs(repo_path);
