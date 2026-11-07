//! Read-only migration preflight. No mapping/apply authority is implied.
use crate::data_format::{DataFormatVersion, CURRENT_DATA_FORMAT};
use rusqlite::{backup::{Backup, StepResult}, types::ValueRef, Connection, OpenFlags};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use crate::preserved_row::{self, Cell};
use std::{collections::BTreeMap, fs::OpenOptions, io::Write, path::Path,
    time::{Duration, Instant}};

pub const INVENTORY_SCHEMA: &str = "frog.migration-inventory.v1";
const MAX_SNAPSHOT_BYTES: u64 = 256 * 1024 * 1024;
const MAX_ROWS: usize = 1_000_000;

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct SchemaObject {
    pub kind: String,
    pub name: String,
    pub table: String,
    pub sql: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Column {
    pub position: i64,
    pub name: String,
    pub declared_type: String,
    pub not_null: bool,
    pub default_sql: Option<String>,
    pub primary_key_position: i64,
    pub hidden: i64,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TableInventory {
    pub name: String,
    pub columns: Vec<Column>,
    pub rows: u64,
    /// Per-column observed SQLite storage classes; no private values in JSON.
    pub storage_classes: BTreeMap<String, BTreeMap<String, u64>>,
    /// Sorted typed-row hashes retain duplicates but ignore physical row order.
    pub content_sha256: String,
    pub disposition: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ForeignKeyViolation {
    pub table: String,
    pub rowid: Option<i64>,
    pub parent: String,
    pub constraint_index: i64,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Inventory {
    pub schema: String,
    pub data_format_version: DataFormatVersion,
    pub importer_build: String,
    pub source_user_version: i64,
    pub snapshot_sha256: String,
    pub snapshot_bytes: u64,
    pub logical_sha256: String,
    pub schema_objects: Vec<SchemaObject>,
    pub tables: Vec<TableInventory>,
    pub integrity: Vec<String>,
    pub foreign_key_violations: Vec<ForeignKeyViolation>,
    pub ready_to_apply: bool,
    pub blockers: Vec<String>,
}

/// A consistent backup includes committed WAL pages. The source is opened
/// read-only, never migrated, checkpointed, or used as the destination.
pub fn inventory(source_path: &Path, snapshot_path: Option<&Path>) -> Result<Inventory, String> {
    let snapshot = capture(source_path)?;
    let report = inventory_snapshot(&snapshot)?;
    if let Some(path) = snapshot_path {
        write_snapshot(&snapshot, path)?;
    }
    Ok(report)
}

pub(crate) fn capture(source_path: &Path) -> Result<Connection, String> {
    let source = Connection::open_with_flags(source_path,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(|e| format!("cannot open source read-only: {e}"))?;
    source.pragma_update(None, "trusted_schema", "OFF").map_err(error)?;
    source.busy_timeout(Duration::from_millis(100)).map_err(error)?;
    let mut snapshot = Connection::open_in_memory().map_err(error)?;
    {
        let page_size: u32 = source.query_row("PRAGMA page_size", [], |r| r.get(0)).map_err(error)?;
        let backup = Backup::new(&source, &mut snapshot).map_err(error)?;
        let started = Instant::now();
        loop {
            if started.elapsed() > Duration::from_secs(30) {
                return Err("source snapshot timed out; retry with a quiescent source".into());
            }
            let step = backup.step(128).map_err(error)?;
            if backup.progress().pagecount as u64 * u64::from(page_size) > MAX_SNAPSHOT_BYTES {
                return Err("source snapshot exceeds 256 MiB inventory limit".into());
            }
            match step {
                StepResult::Done => break,
                StepResult::Busy | StepResult::Locked => std::thread::sleep(Duration::from_millis(10)),
                _ => (),
            }
        }
    }
    snapshot.pragma_update(None, "trusted_schema", "OFF").map_err(error)?;
    snapshot.pragma_update(None, "query_only", "ON").map_err(error)?;
    Ok(snapshot)
}

pub(crate) fn inventory_snapshot(snapshot: &Connection) -> Result<Inventory, String> {
    let bytes = snapshot.serialize("main").map_err(error)?;
    if bytes.len() as u64 > MAX_SNAPSHOT_BYTES {
        return Err("source snapshot exceeds 256 MiB inventory limit".into());
    }
    let snapshot_sha256 = digest(&bytes);
    let source_user_version = snapshot.query_row("PRAGMA user_version", [], |r| r.get(0)).map_err(error)?;
    let schema_objects = snapshot.prepare(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")
        .map_err(error)?.query_map([], |r| Ok(SchemaObject {
            kind: r.get(0)?, name: r.get(1)?, table: r.get(2)?, sql: r.get(3)?,
        })).map_err(error)?.collect::<Result<Vec<_>, _>>().map_err(error)?;
    let mut tables = Vec::new();
    let mut remaining_rows = MAX_ROWS;
    let mut blockers = vec!["full_import_not_implemented".to_string()];
    for object in schema_objects.iter().filter(|o| o.kind == "table") {
        let kind: String = snapshot.query_row(
            "SELECT type FROM pragma_table_list WHERE schema='main' AND name=?1", [&object.name],
            |r| r.get(0)).map_err(error)?;
        if kind == "virtual" || kind == "shadow" {
            return Err(format!("virtual table {} requires an explicit inventory adapter", object.name));
        }
        tables.push(inventory_table(&snapshot, &object.name, &mut remaining_rows)?);
    }
    let integrity = snapshot.prepare("PRAGMA integrity_check").map_err(error)?
        .query_map([], |r| r.get(0)).map_err(error)?
        .collect::<Result<Vec<String>, _>>().map_err(error)?;
    if integrity != ["ok"] { blockers.push("source_integrity_check_failed".into()); }
    let foreign_key_violations = snapshot.prepare("PRAGMA foreign_key_check").map_err(error)?
        .query_map([], |r| Ok(ForeignKeyViolation { table: r.get(0)?, rowid: r.get(1)?,
            parent: r.get(2)?, constraint_index: r.get(3)? }))
        .map_err(error)?.collect::<Result<Vec<_>, _>>().map_err(error)?;
    if !foreign_key_violations.is_empty() { blockers.push("source_foreign_key_violations".into()); }
    let logical_sha256 = digest(&serde_json::to_vec(&(source_user_version, &schema_objects, &tables))
        .map_err(|e| e.to_string())?);
    Ok(Inventory {
        schema: INVENTORY_SCHEMA.into(), data_format_version: CURRENT_DATA_FORMAT.parse()?,
        importer_build: format!("frog-model/{}+{}", env!("CARGO_PKG_VERSION"),
            option_env!("FROG_MODEL_BUILD_ID").unwrap_or("unidentified")),
        source_user_version, snapshot_sha256, snapshot_bytes: bytes.len() as u64,
        logical_sha256, schema_objects, tables, integrity, foreign_key_violations,
        ready_to_apply: false, blockers,
    })
}

// Only publish a snapshot after all preflight work has succeeded. Exclusive
// creation prevents clobbering any file/symlink, including the source itself.
pub(crate) fn write_snapshot(snapshot: &Connection, path: &Path) -> Result<(), String> {
    let bytes = snapshot.serialize("main").map_err(error)?;
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(|e| format!("cannot create new snapshot: {e}"))?;
    file.write_all(&bytes).and_then(|_| file.sync_all())
        .map_err(|e| format!("snapshot write failed (partial file retained): {e}"))
}

fn inventory_table(db: &Connection, name: &str, remaining_rows: &mut usize) -> Result<TableInventory, String> {
    let columns = db.prepare("SELECT cid,name,type,\"notnull\",dflt_value,pk,hidden FROM pragma_table_xinfo(?1) ORDER BY cid")
        .map_err(error)?.query_map([name], |r| Ok(Column {
            position: r.get(0)?, name: r.get(1)?, declared_type: r.get(2)?, not_null: r.get(3)?,
            default_sql: r.get(4)?, primary_key_position: r.get(5)?, hidden: r.get(6)?,
        })).map_err(error)?.collect::<Result<Vec<_>, _>>().map_err(error)?;
    let mut statement = db.prepare(&format!("SELECT * FROM \"{}\"", name.replace('"', "\"\""))).map_err(error)?;
    let names: Vec<String> = statement.column_names().iter().map(|s| s.to_string()).collect();
    let mut storage_classes: BTreeMap<String, BTreeMap<String, u64>> = names.iter()
        .map(|name| (name.clone(), BTreeMap::new())).collect();
    let mut rows = statement.query([]).map_err(error)?;
    let mut hashes = Vec::new();
    while let Some(row) = rows.next().map_err(error)? {
        if *remaining_rows == 0 { return Err("source exceeds 1000000 row inventory limit".into()); }
        *remaining_rows -= 1;
        for (i, name) in names.iter().enumerate() {
            let value = row.get_ref(i).map_err(error)?;
            *storage_classes.get_mut(name).unwrap().entry(cell(value).storage_class().into()).or_default() += 1;
        }
        hashes.push(preserved_row::row_digest(&encode_sql_row(row, names.len())?));
    }
    let content_sha256 = preserved_row::table_digest(&mut hashes);
    Ok(TableInventory { name: name.into(), columns, rows: hashes.len() as u64,
        storage_classes, content_sha256,
        disposition: "requires_explicit_mapping".into() })
}

pub(crate) fn encode_sql_row(row: &rusqlite::Row<'_>, columns: usize) -> Result<Vec<u8>, String> {
    let cells = (0..columns).map(|index| row.get_ref(index).map(cell))
        .collect::<Result<Vec<_>, _>>().map_err(error)?;
    Ok(preserved_row::encode(&cells))
}

fn cell(value: ValueRef<'_>) -> Cell<'_> {
    match value {
        ValueRef::Null => Cell::Null,
        ValueRef::Integer(value) => Cell::Integer(value),
        ValueRef::Real(value) => Cell::RealBits(value.to_bits()),
        ValueRef::Text(value) => Cell::Text(value),
        ValueRef::Blob(value) => Cell::Blob(value),
    }
}

fn digest(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn error(e: rusqlite::Error) -> String { format!("inventory failed: {e}") }
