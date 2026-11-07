-- Applied only to a new projection's owned provenance schema. An unknown source
-- edge-creation time is NULL, never replaced with an import/observation clock.
CREATE TABLE frog_migration_dependency_origins_nullable (
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES frog_migration_sources(source_id),
    source_created_at TEXT,
    mapping_version TEXT NOT NULL,
    actor TEXT NOT NULL,
    PRIMARY KEY(workspace_id, task_id, depends_on),
    FOREIGN KEY(workspace_id, task_id, depends_on)
        REFERENCES frog_model_dependencies(workspace_id, task_id, depends_on)
) STRICT;
INSERT INTO frog_migration_dependency_origins_nullable SELECT * FROM frog_migration_dependency_origins;
DROP TABLE frog_migration_dependency_origins;
ALTER TABLE frog_migration_dependency_origins_nullable RENAME TO frog_migration_dependency_origins;
