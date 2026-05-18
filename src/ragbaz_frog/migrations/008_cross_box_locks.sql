-- cross-box-locks: make a lock meaningful on a box with a different tree.
-- repo_key is the portable repo identity; rel_files_json holds the file
-- paths relative to the repo root, so an overlap is comparable across
-- boxes even though the absolute paths differ.
ALTER TABLE locks ADD COLUMN repo_key TEXT;
ALTER TABLE locks ADD COLUMN rel_files_json TEXT NOT NULL DEFAULT '[]';
CREATE INDEX IF NOT EXISTS idx_locks_repo_key ON locks(repo_key, status);
