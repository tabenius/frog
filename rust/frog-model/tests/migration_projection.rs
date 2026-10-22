#![cfg(feature = "sqlite")]

use frog_model::migration_projection::{project, Projection};
use rusqlite::Connection;
use std::{path::Path,process::Command};
use tempfile::tempdir;

fn source(path: &Path) -> Connection {
    let db = Connection::open(path).unwrap();
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
        CREATE TABLE repos(repo_path TEXT PRIMARY KEY,name TEXT,repo_key TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE tasks(slug TEXT PRIMARY KEY,repo_path TEXT,title TEXT,priority TEXT,workflow_status TEXT,
            assigned_agent TEXT,created_at TEXT,updated_at TEXT,status_confidence_at TEXT,
            why TEXT,what_text TEXT,roi_note TEXT,source TEXT,external_id TEXT,unknown_metadata BLOB);
        CREATE TABLE task_dependencies(task_slug TEXT,depends_on_slug TEXT,relation TEXT,created_at TEXT);
        INSERT INTO repos VALUES('/private/repository','Original repo','repo:one','2026-10-20T00:00:00+00:00','2026-10-20T01:00:00Z');
        INSERT INTO tasks VALUES('first','/private/repository','Private title','p1','review','source-agent',
            '2026-10-20T00:00:00+00:00','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z',
            'Private why',NULL,'Private ROI','github','owner/repo#1',x'00ff');
        INSERT INTO tasks VALUES('second','/private/repository','Second title','p2','done',NULL,
            '2026-10-20T00:00:00Z','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z',
            NULL,'',NULL,NULL,NULL,x'ff00');
        INSERT INTO task_dependencies VALUES('first','second','depends_on','2026-10-20T00:00:00Z');").unwrap();
    db
}

#[test]
fn projection_executes_and_reconciles_core_rows_while_retaining_full_source() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("source.db");
    let snapshot = directory.path().join("snapshot.db");
    let _source = source(&path);
    let before = std::fs::read(&path).unwrap();
    let wal = std::fs::read(directory.path().join("source.db-wal")).unwrap();
    let result = project(&path,"explicit-workspace",Some(&snapshot)).unwrap();
    assert_eq!(result.schema,"frog.migration-projection.v1");
    assert_eq!(result.data_format_version.to_string(),"20261020-6");
    assert_eq!(result.assessment.data_format_version.to_string(),"20261020-4");
    assert_eq!(result.workspace_id,"explicit-workspace");
    assert!(result.preservation.verified);
    assert!(result.model.fields_verified);
    assert!(result.operational_model_populated);
    assert!(!result.ready_to_apply);
    assert!(!result.preservation.operational_parity);
    assert_eq!((result.model.repositories,result.model.tasks,result.model.dependencies,result.model.providers),(1,2,1,1));
    assert_eq!(result.model.initial_revision,0);
    assert_eq!(result.model.source_model_sha256,result.model.destination_model_sha256);
    assert!(result.assessment.fields.iter().any(|f|f.source_column=="unknown_metadata" && f.destination.is_none()));
    let json = serde_json::to_string(&result).unwrap();
    for private in ["Private title","/private/repository","Private why","Private ROI","source-agent","owner/repo#1"] {
        assert!(!json.contains(private),"{private}");
    }
    assert_eq!(std::fs::read(&path).unwrap(),before);
    assert_eq!(std::fs::read(directory.path().join("source.db-wal")).unwrap(),wal);
    assert_eq!(frog_model::migration::inventory(&snapshot,None).unwrap().logical_sha256,result.preservation.logical_sha256);
}

#[test]
fn rejected_rows_are_explicit_and_never_changed_to_a_supported_status() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("source.db");
    let db = source(&path);
    db.execute_batch("UPDATE tasks SET workflow_status='idea' WHERE slug='first';
        UPDATE tasks SET why=zeroblob(8) WHERE slug='second';").unwrap();
    let result = project(&path,"chosen",None).unwrap();
    assert_eq!(result.model.tasks,0);
    assert_eq!(result.model.skipped_tasks,2);
    assert_eq!(result.model.skipped_dependencies,1);
    assert_eq!(result.model.findings.iter().filter(|f|f.table=="tasks").map(|f|f.count).sum::<u64>(),2);
    assert_eq!(result.preservation.rows,4);
    assert!(result.preservation.verified);
    assert_eq!(db.query_row("SELECT workflow_status FROM tasks WHERE slug='first'",[],|r|r.get::<_,String>(0)).unwrap(),"idea");
}

#[test]
fn cli_requires_explicit_workspace_and_only_creates_new_source_snapshots() {
    let directory = tempdir().unwrap();
    let path = directory.path().join("source.db");
    let _source = source(&path);
    let binary = env!("CARGO_BIN_EXE_frog-model-migrate");
    let missing = Command::new(binary).args(["project","--source"]).arg(&path).output().unwrap();
    assert!(!missing.status.success());
    let valid = Command::new(binary).args(["project","--source"]).arg(&path)
        .args(["--workspace","chosen"]).output().unwrap();
    assert!(valid.status.success(),"{}",String::from_utf8_lossy(&valid.stderr));
    let result: Projection = serde_json::from_slice(&valid.stdout).unwrap();
    assert!(result.model.fields_verified);
    assert_eq!(result.model.tasks,2);
    assert!(!Command::new(binary).args(["project","--source"]).arg(&path)
        .args(["--workspace","chosen","--destination","forbidden.db"]).output().unwrap().status.success());
    assert!(!Command::new(binary).args(["inventory","--source"]).arg(&path)
        .args(["--workspace","chosen"]).output().unwrap().status.success());
    assert!(project(&path,"invalid/workspace",None).is_err());
    let before = std::fs::read(&path).unwrap();
    assert!(project(&path,"chosen",Some(&path)).is_err());
    assert_eq!(std::fs::read(&path).unwrap(),before);
}
