-- repo-logical-id: a stable cross-box repo identity decoupled from the
-- absolute path. repo_key is the same on every box (derived from a
-- committed .frogid or the git origin URL); repo_aliases maps that key
-- to whatever local path the repo lives at on a given box.
ALTER TABLE repos ADD COLUMN repo_key TEXT;
CREATE INDEX IF NOT EXISTS idx_repos_repo_key ON repos(repo_key);

CREATE TABLE IF NOT EXISTS repo_aliases (
    repo_key TEXT NOT NULL,
    box TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (repo_key, box, repo_path)
);
CREATE INDEX IF NOT EXISTS idx_repo_aliases_key ON repo_aliases(repo_key);
CREATE INDEX IF NOT EXISTS idx_repo_aliases_box ON repo_aliases(box, repo_path);
