-- Explicit migration provenance; no fabricated source revision or event history.
CREATE TABLE frog_migration_repo_origins (
    workspace_id TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES frog_migration_sources(source_id),
    source_path TEXT NOT NULL,
    source_created_at TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    PRIMARY KEY(workspace_id, repo_id),
    UNIQUE(workspace_id, source_id, source_path),
    FOREIGN KEY(workspace_id, repo_id) REFERENCES frog_model_repos(workspace_id, repo_id)
) STRICT;
CREATE TABLE frog_migration_task_origins (
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES frog_migration_sources(source_id),
    source_created_at TEXT NOT NULL,
    baseline_revision INTEGER NOT NULL CHECK(baseline_revision = 0),
    source_revision INTEGER CHECK(source_revision IS NULL),
    mapping_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    PRIMARY KEY(workspace_id, task_id),
    FOREIGN KEY(workspace_id, task_id) REFERENCES frog_model_tasks(workspace_id, task_id)
) STRICT;
CREATE TABLE frog_migration_dependency_origins (
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES frog_migration_sources(source_id),
    source_created_at TEXT NOT NULL,
    mapping_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    PRIMARY KEY(workspace_id, task_id, depends_on),
    FOREIGN KEY(workspace_id, task_id, depends_on)
        REFERENCES frog_model_dependencies(workspace_id, task_id, depends_on)
) STRICT;
