-- Classify declared cross-repo dependency edges by lifecycle phase.
-- Existing edges were previously unqualified; they are runtime dependencies.
-- Values mirror the `dependencies[].kind` field of ragbaz.component.json.
ALTER TABLE repo_deps ADD COLUMN kind TEXT NOT NULL DEFAULT 'runtime'
    CHECK (kind IN ('install', 'runtime', 'build', 'debug'));
