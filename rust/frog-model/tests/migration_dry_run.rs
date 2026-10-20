#![cfg(feature = "sqlite")]

use frog_model::migration_dry_run::{dry_run, DryRun};
use rusqlite::Connection;
use std::{path::Path, process::Command};
use tempfile::tempdir;

fn fixture(path: &Path) -> Connection {
    let db = Connection::open(path).unwrap();
    db.execute_batch("PRAGMA foreign_keys=OFF;").unwrap();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../src/ragbaz_frog/migrations");
    let mut paths = std::fs::read_dir(root).unwrap().map(|entry| entry.unwrap().path())
        .filter(|p| p.extension().is_some_and(|e| e == "sql")).collect::<Vec<_>>();
    paths.sort();
    for path in paths { db.execute_batch(&std::fs::read_to_string(path).unwrap()).unwrap(); }
    db.execute_batch("INSERT INTO repos(repo_path,name,repo_key,created_at,updated_at)
        VALUES('/private/frog','Frog','repo:frog','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z');
        INSERT INTO tasks(slug,repo_path,title,priority,workflow_status,created_at,updated_at,status_confidence_at)
        VALUES ('one','/private/frog','Private title','p0','todo','2026-10-20T00:00:00+00:00','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z'),
        ('two','/private/frog','Another private title','p2','done','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z');
        INSERT INTO task_dependencies VALUES ('one','two','depends_on','2026-10-20T00:00:00Z');").unwrap();
    db
}

fn count(report: &DryRun, code: &str) -> u64 {
    report.findings.iter().filter(|f| f.code == code).map(|f| f.count).sum()
}

#[test]
fn dry_run_uses_one_snapshot_and_reports_candidate_mapping_without_writes() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let snapshot = dir.path().join("snapshot.db");
    let _db = fixture(&path);
    let before = std::fs::read(&path).unwrap();
    let report = dry_run(&path, Some(&snapshot)).unwrap();
    assert_eq!(report.schema, "frog.migration-dry-run.v1");
    assert_eq!(report.data_format_version.to_string(), "20261020-4");
    assert_eq!(report.inventory.data_format_version.to_string(), "20261020-3");
    assert_eq!(report.candidate_repositories, 1);
    assert_eq!(report.candidate_tasks, 2);
    assert_eq!(report.candidate_dependency_edges, 1);
    assert_eq!(report.normalized_utc_timestamps, 1);
    assert!(!report.ready_to_apply);
    assert!(!report.destination_simulated);
    assert_eq!(count(&report, "full_import_not_implemented"), 1);
    assert_eq!(report.fields.len(), report.inventory.tables.iter().map(|t| t.columns.len()).sum::<usize>());
    assert!(report.fields.iter().any(|f| f.source_table == "task_status_history" && f.destination.is_none()));
    assert!(report.fields.iter().any(|f| f.source_table == "locks" && f.destination.is_none()));
    let json = serde_json::to_string(&report).unwrap();
    assert!(!json.contains("Private title"));
    assert!(!json.contains("/private/frog"));
    assert_eq!(std::fs::read(path).unwrap(), before);
    assert_eq!(frog_model::migration::inventory(&snapshot, None).unwrap().logical_sha256, report.inventory.logical_sha256);
}

#[test]
fn schema_gaps_custom_fields_and_invalid_data_are_explicit() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let db = fixture(&path);
    db.execute_batch("ALTER TABLE tasks ADD COLUMN private_extension BLOB;
        UPDATE tasks SET workflow_status='idea', private_extension=x'00ff' WHERE slug='one';
        UPDATE tasks SET priority='p9',workflow_status='blocked',status_confidence_at='unknown',title=x'ff' WHERE slug='two';").unwrap();
    let report = dry_run(&path, None).unwrap();
    assert_eq!(count(&report, "status_requires_model_schema_extension"), 2);
    assert_eq!(count(&report, "unsupported_priority"), 1);
    assert_eq!(count(&report, "timestamp_requires_explicit_conversion"), 1);
    assert!(count(&report, "mapped_field_storage_class_mismatch") > 0);
    assert!(count(&report, "invalid_storage_class") > 0);
    assert_eq!(report.candidate_tasks, 0);
    assert!(report.findings.iter().any(|f| f.column.as_deref() == Some("private_extension") && f.code == "field_requires_schema_or_preservation"));
}

#[test]
fn identities_are_never_invented_and_ambiguous_repositories_block_import() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let db = fixture(&path);
    db.execute_batch("UPDATE repos SET repo_key=NULL;").unwrap();
    let report = dry_run(&path, None).unwrap();
    assert_eq!(count(&report, "repository_identity_missing"), 1);
    assert_eq!(count(&report, "task_repository_unresolved"), 2);
    assert_eq!(report.candidate_tasks, 0);
    db.execute_batch("UPDATE repos SET repo_key='repo:frog';
        INSERT INTO repos(repo_path,name,repo_key,created_at,updated_at)
        VALUES('/elsewhere','Other','repo:frog','x','x');").unwrap();
    assert_eq!(count(&dry_run(&path, None).unwrap(), "repository_identity_collision"), 1);
}

#[test]
fn graph_assessment_catches_cycles_self_edges_orphans_and_other_relations() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let db = fixture(&path);
    db.execute_batch("INSERT INTO task_dependencies VALUES
        ('two','one','depends_on','x'),('one','one','depends_on','x'),
        ('one','missing','depends_on','x'),('one','two','blocks','x');").unwrap();
    let report = dry_run(&path, None).unwrap();
    assert_eq!(count(&report, "self_dependency"), 1);
    assert_eq!(count(&report, "dangling_dependency"), 1);
    assert_eq!(count(&report, "unsupported_dependency_relation"), 1);
    assert_eq!(count(&report, "dependency_cycle_or_cycle_blocked"), 2);
    assert!(!report.inventory.foreign_key_violations.is_empty());
}

#[test]
fn provider_state_and_missing_columns_are_not_silently_skipped() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let db = fixture(&path);
    db.execute_batch("UPDATE tasks SET source='github' WHERE slug='one';").unwrap();
    assert_eq!(count(&dry_run(&path, None).unwrap(), "incomplete_provider_identity"), 1);
    db.execute_batch("DROP INDEX idx_tasks_source_extid;
        UPDATE tasks SET source='github', external_id='same';").unwrap();
    assert_eq!(count(&dry_run(&path, None).unwrap(), "duplicate_provider_identity"), 1);
    let incomplete = dir.path().join("incomplete.db");
    Connection::open(&incomplete).unwrap().execute_batch("CREATE TABLE tasks(slug TEXT)").unwrap();
    let report = dry_run(&incomplete, None).unwrap();
    assert!(count(&report, "missing_required_column") > 0);
    assert_eq!(report.candidate_tasks, 0);
}

#[test]
fn cli_dry_run_emits_a_blocked_date_versioned_report() {
    let dir = tempdir().unwrap();
    let path = dir.path().join("legacy.db");
    let _db = fixture(&path);
    let result = Command::new(env!("CARGO_BIN_EXE_frog-model-migrate"))
        .args(["dry-run", "--source"]).arg(path).output().unwrap();
    assert!(result.status.success(), "{}", String::from_utf8_lossy(&result.stderr));
    let report: DryRun = serde_json::from_slice(&result.stdout).unwrap();
    assert!(!report.ready_to_apply);
    assert_eq!(report.candidate_tasks, 2);
}
