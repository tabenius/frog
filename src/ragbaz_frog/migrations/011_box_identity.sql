-- box-identity: a stable per-machine identity that survives hostname
-- changes. _box_id() is pinned on first use (defaulting to the current
-- hostname for continuity with existing repo_aliases/data) and persisted
-- outside the DB so it is reachable without a connection. This table is
-- the in-DB record of which box this AGENTS.db belongs to / has seen;
-- locks now carry the originating box_id so a future cross-box reaper
-- can tell a remote lock apart from a local one.
CREATE TABLE IF NOT EXISTS box_identity (
    box_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

ALTER TABLE locks ADD COLUMN box_id TEXT;
CREATE INDEX IF NOT EXISTS idx_locks_box_id ON locks(box_id, status);
