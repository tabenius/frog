#![cfg(feature = "sqlite")]

use frog_model::migration_simulation::{simulate, Simulation};
use rusqlite::Connection;
use std::{path::Path, process::Command};
use tempfile::tempdir;

#[test]
fn full_legacy_schema_is_preserved_without_claiming_operational_parity() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("AGENTS.db");
    let snapshot_path = directory.path().join("snapshot.db");
    let db = Connection::open(&source_path).unwrap();
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;").unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../src/ragbaz_frog/migrations");
    let mut paths = std::fs::read_dir(root).unwrap().map(|entry| entry.unwrap().path())
        .filter(|path| path.extension().is_some_and(|e| e == "sql")).collect::<Vec<_>>();
    paths.sort();
    for path in paths { db.execute_batch(&std::fs::read_to_string(path).unwrap()).unwrap(); }
    db.execute_batch("INSERT INTO repos(repo_path,name,repo_key,created_at,updated_at)
        VALUES('/private/workspace','Frog','repo:frog','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z');
        INSERT INTO agents VALUES ('original-agent','agent','private note','x','x');
        INSERT INTO tasks(slug,repo_path,title,why,workflow_status,assigned_agent,created_at,updated_at,status_confidence_at)
        VALUES ('legacy-task','/private/workspace','Private title','Private reason','idea','original-agent',
        '2026-10-20T00:00:00Z','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z');
        INSERT INTO task_tags VALUES('legacy-task','retained-tag','x');
        INSERT INTO task_status_history(task_slug,workflow_status,note,changed_at) VALUES('legacy-task','idea','private history','x');
        INSERT INTO locks(scope_key,lock_kind,agent_name,started_at,updated_at) VALUES('original-scope','repo','original-agent','x','x');
        ALTER TABLE tasks ADD COLUMN unrecognized_blob BLOB;
        UPDATE tasks SET unrecognized_blob=x'00ff';").unwrap();
    let source_before = std::fs::read(&source_path).unwrap();
    let wal_before = std::fs::read(directory.path().join("AGENTS.db-wal")).unwrap();
    let report = simulate(&source_path, Some(&snapshot_path)).unwrap();
    assert_eq!(report.schema, "frog.migration-simulation.v1");
    assert_eq!(report.data_format_version.to_string(), "20261020-5");
    assert_eq!(report.assessment.data_format_version.to_string(), "20261020-4");
    assert_eq!(report.assessment.inventory.data_format_version.to_string(), "20261020-3");
    assert!(report.destination_simulated);
    assert!(report.preservation.verified);
    assert!(!report.operational_model_populated);
    assert!(!report.ready_to_apply);
    assert!(!report.preservation.operational_parity);
    assert!(report.assessment.findings.iter().any(|finding| finding.code == "status_requires_model_schema_extension"));
    assert_eq!(report.preservation.tables, report.assessment.inventory.tables.len() as u64);
    assert_eq!(report.preservation.rows, report.assessment.inventory.tables.iter().map(|t| t.rows).sum::<u64>());
    assert_eq!(report.preservation.snapshot_sha256, report.assessment.inventory.snapshot_sha256);
    assert_eq!(source_before, std::fs::read(&source_path).unwrap());
    assert_eq!(wal_before, std::fs::read(directory.path().join("AGENTS.db-wal")).unwrap());
    let encoded = serde_json::to_string(&report).unwrap();
    for secret in ["Private title", "Private reason", "original-agent", "/private/workspace", "private history"] {
        assert!(!encoded.contains(secret), "{secret}");
    }
    let snapshot = frog_model::migration::inventory(&snapshot_path, None).unwrap();
    assert_eq!(snapshot.logical_sha256, report.preservation.logical_sha256);
}

#[test]
fn cli_has_no_persistent_destination_and_never_overwrites_source_or_snapshot() {
    let directory = tempdir().unwrap();
    let source = directory.path().join("source.db");
    Connection::open(&source).unwrap().execute_batch("CREATE TABLE unknown(value); INSERT INTO unknown VALUES(x'00ff');").unwrap();
    let before = std::fs::read(&source).unwrap();
    let binary = env!("CARGO_BIN_EXE_frog-model-migrate");
    let output = Command::new(binary).args(["simulate", "--source"]).arg(&source).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let report: Simulation = serde_json::from_slice(&output.stdout).unwrap();
    assert!(report.preservation.verified);
    assert!(!report.ready_to_apply);
    assert!(!Command::new(binary).args(["simulate", "--source"]).arg(&source)
        .args(["--destination", "forbidden.db"]).output().unwrap().status.success());
    assert!(simulate(&source, Some(&source)).is_err());
    assert_eq!(std::fs::read(source).unwrap(), before);
}
