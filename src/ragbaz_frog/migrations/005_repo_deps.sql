-- B3: explicit, declared cross-repo dependency edges. Never inferred --
-- frog deliberately does not do whole-tree static analysis. `dependent`
-- depends on `dependency`; a change in `dependency` makes `dependent`
-- affected too.
CREATE TABLE IF NOT EXISTS repo_deps (
    dependent_repo_path TEXT NOT NULL,
    dependency_repo_path TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (dependent_repo_path, dependency_repo_path)
);

CREATE INDEX IF NOT EXISTS idx_repo_deps_dependency
ON repo_deps(dependency_repo_path);
