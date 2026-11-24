#![cfg(feature = "sqlite")]

use frog_model::migration::{inventory, Inventory};
use rusqlite::Connection;
use sha2::{Digest, Sha256};
use std::process::Command;
use tempfile::tempdir;

#[test]
fn inventory_preserves_unknown_schema_and_typed_values_without_exposing_them() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let db = Connection::open(&path).unwrap();
    db.execute_batch("PRAGMA user_version=17;
        CREATE TABLE \"odd\"\"table\" (\"text\" BLOB, n INTEGER DEFAULT 4, computed TEXT GENERATED ALWAYS AS (hex(\"text\")) STORED);
        INSERT INTO \"odd\"\"table\"(\"text\",n) VALUES('private-do-not-report',NULL),(x'00ff',42),(NULL,3);
        CREATE INDEX odd_index ON \"odd\"\"table\"(n);
        CREATE VIEW odd_view AS SELECT n FROM \"odd\"\"table\";").unwrap();
    let before = std::fs::read(&path).unwrap();
    let result = inventory(&path, None).unwrap();
    assert_eq!(result.source_user_version, 17);
    assert!(!result.ready_to_apply);
    assert_eq!(result.blockers, ["full_import_not_implemented"]);
    let table = &result.tables[0];
    assert_eq!(table.name, "odd\"table");
    assert_eq!(table.rows, 3);
    assert_eq!(table.columns[2].hidden, 3, "stored generated column is inventoried");
    assert_eq!(table.storage_classes["text"]["blob"], 1);
    assert_eq!(table.storage_classes["text"]["text"], 1);
    assert_eq!(table.storage_classes["text"]["null"], 1);
    assert!(result.schema_objects.iter().any(|o| o.kind == "view"));
    assert!(result.schema_objects.iter().any(|o| o.kind == "index"));
    let json = serde_json::to_string(&result).unwrap();
    assert!(!json.contains("private-do-not-report"));
    assert_eq!(serde_json::from_str::<Inventory>(&json).unwrap(), result);
    assert_eq!(std::fs::read(&path).unwrap(), before, "source bytes remain intact");
}

#[test]
fn logical_fingerprints_ignore_row_order_but_preserve_types_duplicates_and_schema() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let db = Connection::open(&path).unwrap();
    db.execute_batch("CREATE TABLE values_table(value); INSERT INTO values_table VALUES (1),('1'),(x'31'),(NULL);").unwrap();
    let first = inventory(&path, None).unwrap();
    db.execute_batch("DELETE FROM values_table; INSERT INTO values_table VALUES (NULL),(x'31'),('1'),(1);").unwrap();
    let reordered = inventory(&path, None).unwrap();
    assert_eq!(first.logical_sha256, reordered.logical_sha256);
    for sql in ["UPDATE values_table SET value=1 WHERE typeof(value)='text'",
        "INSERT INTO values_table VALUES(1)", "ALTER TABLE values_table ADD COLUMN extra TEXT"] {
        let previous = inventory(&path, None).unwrap();
        db.execute_batch(sql).unwrap();
        assert_ne!(previous.logical_sha256, inventory(&path, None).unwrap().logical_sha256);
    }
}

#[test]
fn snapshot_includes_committed_wal_and_can_be_reopened_without_wal_sidecars() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("source.db");
    let copy = dir.path().join("snapshot.db");
    let writer = Connection::open(&path).unwrap();
    writer.execute_batch("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
        CREATE TABLE tasks(id TEXT PRIMARY KEY, title TEXT);
        INSERT INTO tasks VALUES ('wal-task','committed in WAL');").unwrap();
    assert!(dir.path().join("source.db-wal").exists());
    let source_before = std::fs::read(&path).unwrap();
    let wal_before = std::fs::read(dir.path().join("source.db-wal")).unwrap();
    let result = inventory(&path, Some(&copy)).unwrap();
    assert_eq!(result.tables.iter().find(|t| t.name == "tasks").unwrap().rows, 1);
    let bytes = std::fs::read(&copy).unwrap();
    assert_eq!(result.snapshot_sha256, format!("{:x}", Sha256::digest(&bytes)));
    assert_eq!(result.snapshot_bytes, bytes.len() as u64);
    assert_eq!(inventory(&copy, None).unwrap().logical_sha256, result.logical_sha256);
    let copied = Connection::open_with_flags(&copy, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    assert_eq!(copied.query_row("SELECT title FROM tasks", [], |r| r.get::<_, String>(0)).unwrap(), "committed in WAL");
    writer.execute_batch("INSERT INTO tasks VALUES ('later','not in snapshot')").unwrap();
    assert_eq!(copied.query_row("SELECT count(*) FROM tasks", [], |r| r.get::<_, i64>(0)).unwrap(), 1);
    assert_eq!(source_before, std::fs::read(&path).unwrap());
    assert!(std::fs::read(dir.path().join("source.db-wal")).unwrap().starts_with(&wal_before));
}

#[test]
fn inventory_reports_foreign_key_damage_and_never_repairs_it() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("broken.db");
    let db = Connection::open(&path).unwrap();
    db.execute_batch("PRAGMA foreign_keys=OFF; CREATE TABLE parent(id INTEGER PRIMARY KEY);
        CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
        INSERT INTO child VALUES (1,99);").unwrap();
    let result = inventory(&path, None).unwrap();
    assert!(result.blockers.contains(&"source_foreign_key_violations".into()));
    assert_eq!(result.foreign_key_violations[0].parent, "parent");
    assert_eq!(db.query_row("SELECT parent_id FROM child", [], |r| r.get::<_, i64>(0)).unwrap(), 99);
}

#[test]
fn nonexistent_source_and_existing_snapshot_are_never_created_or_overwritten() {
    let dir = tempdir().unwrap();
    let missing = dir.path().join("missing.db");
    assert!(inventory(&missing, None).is_err());
    assert!(!missing.exists());
    let path = dir.path().join("source.db");
    Connection::open(&path).unwrap().execute_batch("CREATE TABLE t(value)").unwrap();
    let before = std::fs::read(&path).unwrap();
    assert!(inventory(&path, Some(&path)).is_err());
    assert_eq!(before, std::fs::read(&path).unwrap());
    let copy = dir.path().join("exists");
    std::fs::write(&copy, b"keep existing data").unwrap();
    assert!(inventory(&path, Some(&copy)).is_err());
    assert_eq!(std::fs::read(copy).unwrap(), b"keep existing data");
}

#[test]
fn actual_legacy_schema_is_fully_inventoried_including_sequences_and_history() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("AGENTS.db");
    let db = Connection::open(&path).unwrap();
    let migrations = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../src/ragbaz_frog/migrations");
    let mut paths = std::fs::read_dir(migrations).unwrap().map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|e| e == "sql")).collect::<Vec<_>>();
    paths.sort();
    for migration in paths {
        db.execute_batch(&std::fs::read_to_string(migration).unwrap()).unwrap();
    }
    db.execute_batch("INSERT INTO tasks(slug,title,created_at,updated_at,status_confidence_at)
        VALUES ('legacy-task','Keep all metadata','2026-10-01','2026-10-02','2026-10-01');
        INSERT INTO task_status_history(task_slug,changed_at,note) VALUES ('legacy-task','2026-10-02','private history');").unwrap();
    let report = inventory(&path, None).unwrap();
    for name in ["tasks", "task_dependencies", "task_conflicts", "task_tags", "task_assignments",
        "task_status_history", "task_files", "locks", "files", "repos", "agents", "sqlite_sequence",
        "units", "repo_targets", "target_runs", "peers", "audit_intent_windows"] {
        assert!(report.tables.iter().any(|t| t.name == name), "missing {name}");
    }
    assert_eq!(report.tables.iter().find(|t| t.name == "task_status_history").unwrap().rows, 1);
}

#[test]
fn cli_requires_explicit_source_and_emits_versioned_inventory() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    Connection::open(&path).unwrap().execute_batch("CREATE TABLE t(v)").unwrap();
    let binary = env!("CARGO_BIN_EXE_frog-model-migrate");
    let output = Command::new(binary).args(["inventory", "--source"]).arg(&path).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let report: Inventory = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(report.data_format_version.to_string(), "20261020-3");
    assert!(!report.ready_to_apply);
    for args in [vec!["inventory"], vec!["apply"], vec!["inventory", "--source"],
        vec!["inventory", "--unknown", "x"]] {
        assert!(!Command::new(binary).args(args).output().unwrap().status.success());
    }
}

#[test]
fn virtual_tables_are_explicitly_refused_before_snapshot_publication() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("virtual.db");
    let snapshot = dir.path().join("snapshot.db");
    Connection::open(&path).unwrap().execute_batch(
        "CREATE /* preserve this comment */ VIRTUAL TABLE documents USING fts5(body);
         INSERT INTO documents VALUES ('private virtual data');").unwrap();
    assert!(inventory(&path, Some(&snapshot)).unwrap_err().contains("virtual table"));
    assert!(!snapshot.exists());
}
