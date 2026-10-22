//! Disposable preservation import. Source SQL is data, not executable DDL.
use crate::{migration::{self, Inventory, SchemaObject, TableInventory},
    migration_dry_run::{self, DryRun}, preserved_row};
use rusqlite::{params, Connection, TransactionBehavior};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap, path::Path};

const PRESERVATION_SCHEMA: &str = include_str!("../sql/0003_migration_preservation.sql");
const MAX_DESTINATION_PAGES: u32 = 131_072; // 512 MiB with fixed 4096-byte pages.

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Preservation {
    pub tables: u64,
    pub columns: u64,
    pub rows: u64,
    pub schema_objects: u64,
    pub typed_row_bytes: u64,
    pub snapshot_bytes: u64,
    pub snapshot_sha256: String,
    pub logical_sha256: String,
    pub verified: bool,
    pub operational_parity: bool,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Simulation {
    pub schema: String,
    pub data_format_version: crate::data_format::DataFormatVersion,
    pub assessment: DryRun,
    pub preservation: Preservation,
    pub destination_simulated: bool,
    pub operational_model_populated: bool,
    pub ready_to_apply: bool,
}

/// There is deliberately no persistent destination parameter. Both preservation
/// and verification execute on a newly allocated, disposable SQLite database.
pub fn simulate(source: &Path, snapshot_path: Option<&Path>) -> Result<Simulation, String> {
    let source = migration::capture(source)?;
    let assessment = migration_dry_run::assess_snapshot(&source)?;
    let mut destination = destination()?;
    preserve(&source, &assessment.inventory, &mut destination)?;
    let preservation = verify(&destination, &assessment.inventory)?;
    if let Some(path) = snapshot_path { migration::write_snapshot(&source, path)?; }
    Ok(Simulation {
        schema: "frog.migration-simulation.v1".into(),
        data_format_version: preserved_row::DATA_FORMAT.parse()?,
        assessment, preservation, destination_simulated: true,
        operational_model_populated: false, ready_to_apply: false,
    })
}

pub(crate) fn destination() -> Result<Connection, String> {
    let db = Connection::open_in_memory().map_err(error)?;
    db.pragma_update(None, "page_size", 4096).map_err(error)?;
    db.pragma_update(None, "max_page_count", MAX_DESTINATION_PAGES).map_err(error)?;
    db.pragma_update(None, "foreign_keys", "ON").map_err(error)?;
    db.pragma_update(None, "trusted_schema", "OFF").map_err(error)?;
    // Only model-owned DDL runs. Unknown legacy table/trigger names remain data.
    db.execute_batch(crate::MIGRATION).map_err(error)?;
    db.execute_batch(crate::SERVICE_MIGRATION).map_err(error)?;
    Ok(db)
}

pub(crate) fn preserve(source: &Connection, inventory: &Inventory, destination: &mut Connection) -> Result<(), String> {
    validate_inventory(inventory)?;
    let foreign_keys: bool = destination.query_row("PRAGMA foreign_keys", [], |r| r.get(0)).map_err(error)?;
    if !foreign_keys { return Err("preservation requires foreign-key enforcement".into()); }
    let transaction = destination.transaction_with_behavior(TransactionBehavior::Immediate).map_err(error)?;
    transaction.execute_batch(PRESERVATION_SCHEMA).map_err(error)?;
    let source_id = &inventory.snapshot_sha256;
    let snapshot = source.serialize("main").map_err(error)?;
    if digest(&snapshot) != *source_id { return Err("snapshot fingerprint changed before preservation".into()); }
    transaction.execute("INSERT INTO frog_migration_sources VALUES (?1,?2,?3,?4)",
        params![source_id, preserved_row::DATA_FORMAT, json(inventory)?, &*snapshot]).map_err(error)?;
    for (ordinal, object) in inventory.schema_objects.iter().enumerate() {
        transaction.execute("INSERT INTO frog_migration_schema_objects VALUES (?1,?2,?3,?4,?5,?6)",
            params![source_id, ordinal as i64, object.kind, object.name, object.table, object.sql]).map_err(error)?;
    }
    for table in &inventory.tables {
        transaction.execute("INSERT INTO frog_migration_tables VALUES (?1,?2,?3)",
            params![source_id, table.name, json(table)?]).map_err(error)?;
        let mut statement = source.prepare(&format!("SELECT * FROM \"{}\"", table.name.replace('"', "\"\""))).map_err(error)?;
        let columns = statement.column_count();
        let mut rows = statement.query([]).map_err(error)?;
        let mut ordinal = 0i64;
        let mut insert = transaction.prepare_cached("INSERT INTO frog_migration_rows VALUES (?1,?2,?3,?4)").map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            let payload = migration::encode_sql_row(row, columns)?;
            insert.execute(params![source_id, table.name, ordinal, payload]).map_err(error)?;
            ordinal += 1;
        }
        if ordinal as u64 != table.rows { return Err("source row count changed before preservation".into()); }
    }
    // Verify inside the same transaction: a mismatch rolls back even the DDL.
    verify(&transaction, inventory)?;
    transaction.commit().map_err(error)
}

fn validate_inventory(expected: &Inventory) -> Result<(), String> {
    if expected.schema != migration::INVENTORY_SCHEMA {
        return Err("unsupported inventory schema".into());
    }
    crate::data_format::require_current(&expected.data_format_version.to_string())?;
    Ok(())
}

pub(crate) fn verify(destination: &Connection, expected: &Inventory) -> Result<Preservation, String> {
    validate_inventory(expected)?;
    let (source_id, format, inventory, snapshot_bytes, snapshot_sha256): (String, String, String, u64, String) = {
        let mut statement = destination.prepare(
            "SELECT source_id,data_format_version,inventory_json,snapshot FROM frog_migration_sources").map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        let row = rows.next().map_err(error)?.ok_or("preserved source is missing")?;
        let snapshot = row.get_ref(3).map_err(error)?.as_blob().map_err(|_| "preserved snapshot is not a blob")?;
        (row.get(0).map_err(error)?, row.get(1).map_err(error)?, row.get(2).map_err(error)?,
            snapshot.len() as u64, digest(snapshot))
    };
    if source_id != expected.snapshot_sha256 || format != preserved_row::DATA_FORMAT
        || serde_json::from_str::<Inventory>(&inventory).map_err(|e| e.to_string())? != *expected
        || snapshot_bytes != expected.snapshot_bytes || snapshot_sha256 != expected.snapshot_sha256 {
        return Err("preserved source metadata or snapshot fingerprint mismatch".into());
    }
    let mut objects_statement = destination.prepare(
        "SELECT ordinal,kind,name,table_name,sql FROM frog_migration_schema_objects WHERE source_id=?1 ORDER BY ordinal").map_err(error)?;
    let stored_objects = objects_statement.query_map([&source_id], |row| Ok((row.get::<_, i64>(0)?, SchemaObject {
        kind: row.get(1)?, name: row.get(2)?, table: row.get(3)?, sql: row.get(4)?,
    }))).map_err(error)?.collect::<Result<Vec<_>, _>>().map_err(error)?;
    if stored_objects.iter().enumerate().any(|(index, (ordinal, _))| *ordinal != index as i64) {
        return Err("preserved schema ordinal mismatch".into());
    }
    let objects: Vec<_> = stored_objects.into_iter().map(|(_, object)| object).collect();
    if objects != expected.schema_objects { return Err("preserved schema metadata mismatch".into()); }
    for (table, count) in [("frog_migration_sources", 1),
        ("frog_migration_tables", expected.tables.len() as i64),
        ("frog_migration_schema_objects", expected.schema_objects.len() as i64)] {
        let actual: i64 = destination.query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0)).map_err(error)?;
        if actual != count { return Err("unexpected preservation metadata records".into()); }
    }
    let mut total_rows = 0;
    let mut total_bytes = 0;
    for table in &expected.tables {
        let metadata: String = destination.query_row(
            "SELECT metadata_json FROM frog_migration_tables WHERE source_id=?1 AND name=?2",
            params![source_id, table.name], |r| r.get(0)).map_err(error)?;
        if serde_json::from_str::<TableInventory>(&metadata).map_err(|e| e.to_string())? != *table {
            return Err("preserved table/column metadata mismatch".into());
        }
        let mut statement = destination.prepare(
            "SELECT ordinal,payload FROM frog_migration_rows WHERE source_id=?1 AND table_name=?2 ORDER BY ordinal").map_err(error)?;
        let mut rows = statement.query(params![source_id, table.name]).map_err(error)?;
        let mut hashes = Vec::new();
        let mut classes: BTreeMap<String, BTreeMap<String, u64>> = table.columns.iter()
            .filter(|c| c.hidden != 1).map(|c| (c.name.clone(), BTreeMap::new())).collect();
        let columns: Vec<_> = table.columns.iter().filter(|c| c.hidden != 1).collect();
        while let Some(row) = rows.next().map_err(error)? {
            if row.get::<_, i64>(0).map_err(error)? != hashes.len() as i64 {
                return Err("preserved row ordinal mismatch".into());
            }
            let payload = row.get_ref(1).map_err(error)?.as_blob().map_err(|_| "preserved payload is not a blob")?;
            let cells = preserved_row::decode(payload)?;
            if cells.len() != columns.len() { return Err("preserved column count mismatch".into()); }
            for (column, cell) in columns.iter().zip(cells) {
                *classes.get_mut(&column.name).unwrap().entry(cell.storage_class().into()).or_default() += 1;
            }
            total_bytes += payload.len() as u64;
            hashes.push(preserved_row::row_digest(payload));
        }
        if hashes.len() as u64 != table.rows || classes != table.storage_classes
            || preserved_row::table_digest(&mut hashes) != table.content_sha256 {
            return Err(format!("preserved table content mismatch: {}", table.name));
        }
        total_rows += hashes.len() as u64;
    }
    let actual_rows: i64 = destination.query_row("SELECT count(*) FROM frog_migration_rows", [], |r| r.get(0)).map_err(error)?;
    if actual_rows as u64 != total_rows { return Err("unexpected preservation row records".into()); }
    let integrity: String = destination.query_row("PRAGMA integrity_check", [], |r| r.get(0)).map_err(error)?;
    let foreign_key_errors = destination.prepare("PRAGMA foreign_key_check").map_err(error)?
        .query_map([], |_| Ok(())).map_err(error)?.count();
    if integrity != "ok" || foreign_key_errors != 0 { return Err("destination integrity check failed".into()); }
    Ok(Preservation {
        tables: expected.tables.len() as u64,
        columns: expected.tables.iter().map(|t| t.columns.len() as u64).sum(),
        rows: total_rows, schema_objects: objects.len() as u64, typed_row_bytes: total_bytes,
        snapshot_bytes: expected.snapshot_bytes, snapshot_sha256: expected.snapshot_sha256.clone(),
        logical_sha256: expected.logical_sha256.clone(), verified: true, operational_parity: false,
    })
}

fn digest(bytes: &[u8]) -> String { format!("{:x}", Sha256::digest(bytes)) }
fn json<T: Serialize>(value: &T) -> Result<String, String> { serde_json::to_string(value).map_err(|e| e.to_string()) }
fn error(error: rusqlite::Error) -> String { format!("preservation simulation failed: {error}") }

#[cfg(test)]
mod tests {
    use super::*;
    use crate::preserved_row::Cell;

    fn source() -> Connection {
        let db = Connection::open_in_memory().unwrap();
        db.execute_batch("CREATE TABLE values_table(value);
            INSERT INTO values_table VALUES(NULL),(9223372036854775807),(-9223372036854775808),
                (1.5),(CAST(x'ff00' AS TEXT)),(x'ff00'),(''),(x''),('duplicate'),('duplicate');
            CREATE TABLE generated(id INTEGER PRIMARY KEY AUTOINCREMENT, value TEXT,
                stored TEXT GENERATED ALWAYS AS (hex(value)) STORED,
                virtual TEXT GENERATED ALWAYS AS (length(value)) VIRTUAL);
            INSERT INTO generated(value) VALUES ('retained');
            CREATE TABLE keyed(id TEXT PRIMARY KEY,value BLOB) WITHOUT ROWID;
            INSERT INTO keyed VALUES ('key',x'00ff');
            CREATE VIEW unsafe_view AS SELECT load_extension('/never/execute');
            CREATE TABLE frog_model_tasks(secret TEXT);
            INSERT INTO frog_model_tasks VALUES ('source-table-is-data');
            CREATE TRIGGER source_trigger AFTER INSERT ON values_table
                BEGIN DELETE FROM frog_model_tasks; END;").unwrap();
        db
    }

    #[test]
    fn preservation_roundtrips_all_storage_classes_and_keeps_source_schema_inert() {
        let source = source();
        let expected = migration::inventory_snapshot(&source).unwrap();
        let before = source.serialize("main").unwrap().to_vec();
        let mut target = destination().unwrap();
        preserve(&source, &expected, &mut target).unwrap();
        let result = verify(&target, &expected).unwrap();
        assert!(result.verified);
        assert!(!result.operational_parity);
        assert_eq!(result.rows, expected.tables.iter().map(|t| t.rows).sum::<u64>());
        let mut statement = target.prepare("SELECT payload FROM frog_migration_rows WHERE table_name='values_table' ORDER BY ordinal").unwrap();
        let payloads = statement.query_map([], |r| r.get::<_, Vec<u8>>(0)).unwrap()
            .collect::<Result<Vec<_>, _>>().unwrap();
        assert_eq!(preserved_row::decode(&payloads[1]).unwrap(), [Cell::Integer(i64::MAX)]);
        assert_eq!(preserved_row::decode(&payloads[2]).unwrap(), [Cell::Integer(i64::MIN)]);
        assert_eq!(preserved_row::decode(&payloads[4]).unwrap(), [Cell::Text(b"\xff\x00")]);
        assert_eq!(preserved_row::decode(&payloads[5]).unwrap(), [Cell::Blob(b"\xff\x00")]);
        assert_eq!(payloads[8], payloads[9], "duplicate rows are kept, not deduplicated");
        assert_eq!(target.query_row("SELECT count(*) FROM frog_model_tasks", [], |r| r.get::<_, i64>(0)).unwrap(), 0);
        assert_eq!(target.query_row("SELECT count(*) FROM sqlite_schema WHERE name IN ('unsafe_view','source_trigger','keyed')", [], |r| r.get::<_, i64>(0)).unwrap(), 0);
        assert_eq!(source.serialize("main").unwrap().to_vec(), before);
    }

    #[test]
    fn verifier_detects_content_metadata_snapshot_and_version_tampering() {
        for sql in [
            "UPDATE frog_migration_rows SET payload=x'00' WHERE table_name='values_table' AND ordinal=0",
            "DELETE FROM frog_migration_rows WHERE table_name='values_table' AND ordinal=0",
            "UPDATE frog_migration_rows SET ordinal=100 WHERE table_name='values_table' AND ordinal=0",
            "UPDATE frog_migration_tables SET metadata_json='{}' WHERE name='values_table'",
            "UPDATE frog_migration_sources SET snapshot=x'00'",
            "UPDATE frog_migration_sources SET inventory_json='{}'",
            "UPDATE frog_migration_sources SET data_format_version='20261020-6'",
            "UPDATE frog_migration_schema_objects SET sql='changed' WHERE ordinal=0",
            "UPDATE frog_migration_schema_objects SET ordinal=1000 WHERE ordinal=(SELECT max(ordinal) FROM frog_migration_schema_objects)",
        ] {
            let source = source();
            let expected = migration::inventory_snapshot(&source).unwrap();
            let mut target = destination().unwrap();
            preserve(&source, &expected, &mut target).unwrap();
            target.execute_batch(sql).unwrap();
            assert!(verify(&target, &expected).is_err(), "{sql}");
        }
        let source = source();
        let expected = migration::inventory_snapshot(&source).unwrap();
        let mut target = destination().unwrap();
        preserve(&source, &expected, &mut target).unwrap();
        let replacement = preserved_row::encode(&[Cell::Text(b"different-but-well-formed")]);
        target.execute("UPDATE frog_migration_rows SET payload=?1 WHERE table_name='values_table' AND ordinal=0", [replacement]).unwrap();
        assert!(verify(&target, &expected).unwrap_err().contains("content mismatch"));
    }

    #[test]
    fn failed_reconciliation_rolls_back_rows_and_preservation_ddl() {
        let source = source();
        let mut expected = migration::inventory_snapshot(&source).unwrap();
        expected.tables[0].content_sha256 = "not-the-source-digest".into();
        let mut target = destination().unwrap();
        assert!(preserve(&source, &expected, &mut target).is_err());
        assert_eq!(target.query_row("SELECT count(*) FROM sqlite_schema WHERE name LIKE 'frog_migration_%'", [], |r| r.get::<_, i64>(0)).unwrap(), 0);
        assert!(target.prepare("SELECT * FROM frog_model_tasks").is_ok());
    }

    #[test]
    fn collisions_and_capacity_failures_leave_existing_destination_state_intact() {
        let source = source();
        let expected = migration::inventory_snapshot(&source).unwrap();
        let mut target = destination().unwrap();
        target.execute_batch("CREATE TABLE frog_migration_rows(keep TEXT); INSERT INTO frog_migration_rows VALUES('keep');").unwrap();
        assert!(preserve(&source, &expected, &mut target).is_err());
        assert_eq!(target.query_row("SELECT keep FROM frog_migration_rows", [], |r| r.get::<_, String>(0)).unwrap(), "keep");
        assert!(target.prepare("SELECT * FROM frog_migration_sources").is_err());
        let source = Connection::open_in_memory().unwrap();
        source.execute_batch("CREATE TABLE large(value BLOB); INSERT INTO large VALUES(zeroblob(1048576));").unwrap();
        let expected = migration::inventory_snapshot(&source).unwrap();
        let mut target = destination().unwrap();
        let pages: u32 = target.query_row("PRAGMA page_count", [], |r| r.get(0)).unwrap();
        target.pragma_update(None, "max_page_count", pages + 16).unwrap();
        assert!(preserve(&source, &expected, &mut target).is_err());
        assert!(target.prepare("SELECT * FROM frog_migration_sources").is_err());
        assert!(target.prepare("SELECT * FROM frog_model_tasks").is_ok());
    }

    #[test]
    fn incompatible_inventory_is_rejected_before_any_destination_write() {
        let source = source();
        let mut expected = migration::inventory_snapshot(&source).unwrap();
        expected.data_format_version = "20261020-4".parse().unwrap();
        let mut target = destination().unwrap();
        assert!(preserve(&source, &expected, &mut target).is_err());
        assert!(target.prepare("SELECT * FROM frog_migration_sources").is_err());
    }
}
