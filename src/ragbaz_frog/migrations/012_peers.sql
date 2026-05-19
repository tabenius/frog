-- frog-join: registry of federated peer boxes. A peer is another box
-- running its own AGENTS.db; we reach it over SSH (no daemon, no shared
-- file). repo_aliases gains that peer's (repo_key -> remote path)
-- entries on join so `whereis` resolves cross-box; this table is the
-- list of peers `sync`/federation-aware scheduling will iterate.
CREATE TABLE IF NOT EXISTS peers (
    box_id TEXT PRIMARY KEY,
    hostname TEXT,
    ssh_target TEXT NOT NULL,
    remote_db TEXT,
    added_at TEXT NOT NULL,
    last_join_at TEXT NOT NULL
);
