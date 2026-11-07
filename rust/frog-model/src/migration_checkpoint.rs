//! Durable staging, never activation of an incomplete operational projection.
//! Resume verification reconstructs the expected database from an explicit source
//! instead of trusting a checkpoint's own claims about its contents.
use crate::{
    migration,
    migration_projection::{self, Projection},
};
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::Path;

pub const DATA_FORMAT: &str = "20261107-7";
const SCHEMA: &str = "frog.migration-checkpoint.v1";

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Checkpoint {
    pub schema: String,
    pub data_format_version: crate::data_format::DataFormatVersion,
    pub projection: Projection,
    pub checkpoint_logical_sha256: String,
    pub checkpoint_verified: bool,
    pub ready_to_apply: bool,
}

fn expected(source: &Path, workspace: &str) -> Result<(Checkpoint, Connection), String> {
    let source = migration::capture(source)?;
    let (projection, destination) = migration_projection::build(&source, workspace)?;
    destination
        .execute_batch(
            "CREATE TABLE frog_migration_checkpoint(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        schema TEXT NOT NULL, data_format_version TEXT NOT NULL,
        workspace_id TEXT NOT NULL, source_sha256 TEXT NOT NULL
    ) STRICT;",
        )
        .map_err(error)?;
    destination
        .execute(
            "INSERT INTO frog_migration_checkpoint VALUES(1,?1,?2,?3,?4)",
            params![
                SCHEMA,
                DATA_FORMAT,
                workspace,
                projection.preservation.snapshot_sha256
            ],
        )
        .map_err(error)?;
    // This also applies the 256 MiB / one-million-row staging limits before
    // creating a file. The envelope and projected rows count toward these limits.
    let inventory = migration::inventory_snapshot(&destination)?;
    Ok((
        Checkpoint {
            schema: SCHEMA.into(),
            data_format_version: DATA_FORMAT.parse()?,
            projection,
            checkpoint_logical_sha256: inventory.logical_sha256,
            checkpoint_verified: false,
            ready_to_apply: false,
        },
        destination,
    ))
}

fn verify_file(path: &Path, expected: &Checkpoint) -> Result<(), String> {
    // Capture includes committed WAL state and is read-only. Never execute DDL
    // from the stored source, replay operations or repair the checkpoint.
    let database = migration::capture(path)?;
    let actual = migration::inventory_snapshot(&database)?;
    if actual.integrity != ["ok"]
        || !actual.foreign_key_violations.is_empty()
        || actual.logical_sha256 != expected.checkpoint_logical_sha256
    {
        return Err(
            "checkpoint does not match the explicit source, workspace or supported format".into(),
        );
    }
    Ok(())
}

/// Write a new staging database exclusively and verify the persisted bytes.
/// Existing files, symlinks and source aliases are never replaced. A failed
/// write/verification retains the artifact for inspection and reports failure.
pub fn stage(source: &Path, workspace: &str, checkpoint: &Path) -> Result<Checkpoint, String> {
    let (mut report, destination) = expected(source, workspace)?;
    migration::write_snapshot(&destination, checkpoint)?;
    verify_file(checkpoint, &report)?;
    report.checkpoint_verified = true;
    Ok(report)
}

/// Resume by validating a checkpoint against a freshly reconstructed projection.
/// Source identity is anchored in the explicitly supplied source snapshot, not
/// in editable metadata inside the checkpoint. Nothing is written on resume.
pub fn resume(source: &Path, workspace: &str, checkpoint: &Path) -> Result<Checkpoint, String> {
    let (mut report, _) = expected(source, workspace)?;
    verify_file(checkpoint, &report)?;
    report.checkpoint_verified = true;
    Ok(report)
}

fn error(error: rusqlite::Error) -> String {
    format!("migration checkpoint failed: {error}")
}
