-- Simulation/import infrastructure, not an automatic upgrade of user data.
-- Source DDL is stored as data; never execute source triggers/views/defaults.
CREATE TABLE frog_migration_sources (
    source_id TEXT PRIMARY KEY,
    data_format_version TEXT NOT NULL,
    inventory_json TEXT NOT NULL,
    snapshot BLOB NOT NULL
) STRICT;
CREATE TABLE frog_migration_schema_objects (
    source_id TEXT NOT NULL REFERENCES frog_migration_sources(source_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    sql TEXT,
    PRIMARY KEY(source_id, ordinal)
) STRICT;
CREATE TABLE frog_migration_tables (
    source_id TEXT NOT NULL REFERENCES frog_migration_sources(source_id),
    name TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    PRIMARY KEY(source_id, name)
) STRICT;
CREATE TABLE frog_migration_rows (
    source_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    payload BLOB NOT NULL,
    PRIMARY KEY(source_id, table_name, ordinal),
    FOREIGN KEY(source_id, table_name) REFERENCES frog_migration_tables(source_id, name)
) STRICT;
