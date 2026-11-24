#![cfg(feature = "sqlite")]

use frog_model::migration_checkpoint::{resume, stage, Checkpoint};
use rusqlite::Connection;
use std::{path::Path, process::Command};
use tempfile::tempdir;

fn source(path: &Path) -> Connection {
    let db = Connection::open(path).unwrap();
    db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
        CREATE TABLE repos(repo_path TEXT PRIMARY KEY,name TEXT,repo_key TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE tasks(slug TEXT PRIMARY KEY,repo_path TEXT,title TEXT,priority TEXT,workflow_status TEXT,
            assigned_agent TEXT,created_at TEXT,updated_at TEXT,status_confidence_at TEXT);
        CREATE TABLE task_dependencies(task_slug TEXT,depends_on_slug TEXT,relation TEXT,created_at TEXT);
        CREATE TABLE unknown_data(value);
        INSERT INTO unknown_data VALUES(CAST(x'ff00' AS TEXT)),(9223372036854775807),(x'0011');
        INSERT INTO repos VALUES('/private/source','Source repo','repo:one','2026-10-20T00:00:00Z','2026-10-20T01:00:00Z');
        INSERT INTO tasks VALUES('active','/private/source','Private title','p1','review','original-agent',
            '2026-10-20T00:00:00Z','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z'),
            ('unsupported','/private/source','Blocked','p2','blocked',NULL,
            '2026-10-20T00:00:00Z','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z');").unwrap();
    db
}

#[test]
fn durable_checkpoint_resumes_without_replay_or_source_writes() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let checkpoint = directory.path().join("checkpoint.db");
    let _source = source(&source_path);
    let before = std::fs::read(&source_path).unwrap();
    let wal = std::fs::read(directory.path().join("source.db-wal")).unwrap();
    let staged = stage(&source_path, "chosen", &checkpoint).unwrap();
    assert_eq!(staged.schema, "frog.migration-checkpoint.v1");
    assert_eq!(staged.data_format_version.to_string(), "20261107-7");
    assert!(staged.checkpoint_verified);
    assert!(!staged.ready_to_apply);
    assert!(!staged.projection.ready_to_apply);
    assert!(!staged.projection.preservation.operational_parity);
    assert_eq!(staged.projection.model.tasks, 1);
    assert_eq!(staged.projection.model.skipped_tasks, 1);
    let stored = std::fs::read(&checkpoint).unwrap();
    for _ in 0..2 {
        let resumed = resume(&source_path, "chosen", &checkpoint).unwrap();
        assert!(resumed.checkpoint_verified);
        assert_eq!(
            resumed.checkpoint_logical_sha256,
            staged.checkpoint_logical_sha256
        );
        assert_eq!(std::fs::read(&checkpoint).unwrap(), stored);
    }
    assert_eq!(std::fs::read(&source_path).unwrap(), before);
    assert_eq!(
        std::fs::read(directory.path().join("source.db-wal")).unwrap(),
        wal
    );
    let json = serde_json::to_string(&staged).unwrap();
    for private in ["Private title", "original-agent", "/private/source"] {
        assert!(!json.contains(private));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            std::fs::metadata(&checkpoint).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}

#[test]
fn stage_refuses_existing_files_source_aliases_and_symlinks() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let _source = source(&source_path);
    let checkpoint = directory.path().join("checkpoint.db");
    stage(&source_path, "chosen", &checkpoint).unwrap();
    let before = std::fs::read(&checkpoint).unwrap();
    assert!(stage(&source_path, "chosen", &checkpoint).is_err());
    assert_eq!(std::fs::read(&checkpoint).unwrap(), before);
    let before = std::fs::read(&source_path).unwrap();
    assert!(stage(&source_path, "chosen", &source_path).is_err());
    let alias = directory.path().join("alias.db");
    std::fs::hard_link(&source_path, &alias).unwrap();
    assert!(stage(&source_path, "chosen", &alias).is_err());
    #[cfg(unix)]
    {
        let symlink = directory.path().join("symlink.db");
        std::os::unix::fs::symlink(&source_path, &symlink).unwrap();
        assert!(stage(&source_path, "chosen", &symlink).is_err());
        std::fs::remove_file(&symlink).unwrap();
        std::os::unix::fs::symlink(directory.path().join("missing.db"), &symlink).unwrap();
        assert!(stage(&source_path, "chosen", &symlink).is_err());
        assert!(!directory.path().join("missing.db").exists());
    }
    assert_eq!(std::fs::read(&source_path).unwrap(), before);
}

#[test]
fn resume_refuses_changed_source_workspace_and_missing_checkpoint() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let source = source(&source_path);
    let checkpoint = directory.path().join("checkpoint.db");
    stage(&source_path, "chosen", &checkpoint).unwrap();
    let before = std::fs::read(&checkpoint).unwrap();
    assert!(resume(&source_path, "other-workspace", &checkpoint).is_err());
    let missing = directory.path().join("missing.db");
    assert!(resume(&source_path, "chosen", &missing).is_err());
    assert!(!missing.exists());
    source
        .execute_batch("UPDATE tasks SET title='Changed' WHERE slug='active'")
        .unwrap();
    assert!(resume(&source_path, "chosen", &checkpoint).is_err());
    assert_eq!(std::fs::read(&checkpoint).unwrap(), before);
}

#[test]
fn immutable_source_snapshot_allows_resume_after_the_live_source_changes() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let source = source(&source_path);
    let snapshot = directory.path().join("reviewed-source.db");
    frog_model::migration::inventory(&source_path, Some(&snapshot)).unwrap();
    let checkpoint = directory.path().join("checkpoint.db");
    stage(&snapshot, "chosen", &checkpoint).unwrap();
    source
        .execute_batch("UPDATE tasks SET title='Later live work'")
        .unwrap();
    assert!(resume(&source_path, "chosen", &checkpoint).is_err());
    assert!(
        resume(&snapshot, "chosen", &checkpoint)
            .unwrap()
            .checkpoint_verified
    );
    // The verification snapshot must include committed WAL edits too.
    let db = Connection::open(&checkpoint).unwrap();
    db.execute_batch(
        "PRAGMA journal_mode=WAL; PRAGMA wal_autocheckpoint=0;
        UPDATE frog_model_tasks SET title='Changed in WAL';",
    )
    .unwrap();
    let wal = directory.path().join("checkpoint.db-wal");
    let before = std::fs::read(&wal).unwrap();
    assert!(resume(&snapshot, "chosen", &checkpoint).is_err());
    assert_eq!(std::fs::read(&wal).unwrap(), before);
}

#[test]
fn failed_projection_does_not_create_a_checkpoint() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let source = source(&source_path);
    // Use two valid projected endpoints forming a cycle
    // to force the seed transaction to fail rather than publishing partial work.
    source.execute_batch("UPDATE tasks SET workflow_status='todo' WHERE slug='unsupported';
        INSERT INTO task_dependencies VALUES('active','unsupported','depends_on','2026-10-20T00:00:00Z'),
            ('unsupported','active','depends_on','2026-10-20T00:00:00Z');").unwrap();
    let checkpoint = directory.path().join("checkpoint.db");
    assert!(stage(&source_path, "chosen", &checkpoint).is_err());
    assert!(!checkpoint.exists());
}

#[test]
fn resume_detects_archive_model_receipt_schema_and_format_tampering() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let _source = source(&source_path);
    for (index, sql) in [
        "UPDATE frog_model_tasks SET title='Tampered'",
        "UPDATE frog_model_tasks SET revision=1",
        "UPDATE frog_model_operations SET request_key='Tampered'",
        "UPDATE frog_migration_task_origins SET actor='other'",
        "UPDATE frog_migration_rows SET payload=x'00' WHERE ordinal=0",
        "UPDATE frog_migration_sources SET snapshot=x'00'",
        "UPDATE frog_migration_checkpoint SET data_format_version='20990101-1'",
        "UPDATE frog_migration_checkpoint SET workspace_id='other'",
        "CREATE TABLE unexpected(value); INSERT INTO unexpected VALUES('extra')",
        "CREATE VIEW unexpected AS SELECT load_extension('/never/execute')",
        "CREATE TRIGGER unexpected AFTER INSERT ON frog_model_tasks BEGIN DELETE FROM frog_model_tasks; END",
    ].iter().enumerate() {
        let checkpoint = directory.path().join(format!("checkpoint-{index}.db"));
        stage(&source_path, "chosen", &checkpoint).unwrap();
        let db = Connection::open(&checkpoint).unwrap();
        db.execute_batch(sql).unwrap();
        drop(db);
        let before = std::fs::read(&checkpoint).unwrap();
        assert!(resume(&source_path, "chosen", &checkpoint).is_err(), "{sql}");
        assert_eq!(std::fs::read(&checkpoint).unwrap(), before);
    }
}

#[test]
fn cli_roundtrip_requires_explicit_paths_and_does_not_publish_failed_artifacts() {
    let directory = tempdir().unwrap();
    let source_path = directory.path().join("source.db");
    let _source = source(&source_path);
    let checkpoint = directory.path().join("checkpoint.db");
    let binary = env!("CARGO_BIN_EXE_frog-model-migrate");
    for operation in ["stage", "resume"] {
        for flags in [
            vec![],
            vec!["--workspace", "chosen"],
            vec!["--checkpoint", "unused.db"],
        ] {
            let output = Command::new(binary)
                .arg(operation)
                .arg("--source")
                .arg(&source_path)
                .args(flags)
                .output()
                .unwrap();
            assert!(!output.status.success());
            assert!(output.stdout.is_empty());
        }
    }
    for operation in ["stage", "resume"] {
        let output = Command::new(binary)
            .args([operation, "--source"])
            .arg(&source_path)
            .args(["--workspace", "chosen", "--checkpoint"])
            .arg(&checkpoint)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let report: Checkpoint = serde_json::from_slice(&output.stdout).unwrap();
        assert!(report.checkpoint_verified);
        assert!(!report.ready_to_apply);
    }
    let before = std::fs::read(&checkpoint).unwrap();
    let output = Command::new(binary)
        .args(["stage", "--source"])
        .arg(&source_path)
        .args(["--workspace", "chosen", "--checkpoint"])
        .arg(&checkpoint)
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert_eq!(std::fs::read(&checkpoint).unwrap(), before);
    let invalid = directory.path().join("invalid.db");
    let output = Command::new(binary)
        .args(["stage", "--source"])
        .arg(&source_path)
        .args(["--workspace", "chosen", "--checkpoint"])
        .arg(&invalid)
        .args(["--snapshot", "forbidden.db"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(!invalid.exists());
}
