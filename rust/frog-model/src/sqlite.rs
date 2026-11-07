//! Native adapter: ordinary SQLite transactions; WAL is optional, not a contract.
use rusqlite::{types::Value as SqlValue, Connection, TransactionBehavior};
use serde_json::{Map, Value};

/// Surface the engine's own diagnostic, bounded: it names the violated
/// constraint, never caller data. A failing plan is a bug or a contract
/// mismatch, so hiding it as "operation failed" only slows that down.
fn describe(error: rusqlite::Error) -> String {
    let detail = match &error {
        rusqlite::Error::SqliteFailure(_, Some(message)) => message.as_str(),
        _ => "",
    };
    let summary = format!("atomic operation failed ({error})");
    if detail.is_empty() {
        summary
    } else {
        format!(
            "{summary}: {}",
            detail.chars().take(200).collect::<String>()
        )
    }
}

/// JSON-encoded list columns of this model. SQLite returns them as text; hosts
/// must not have to know that, so the model's own adapters decode them.
const JSON_LIST_COLUMNS: [&str; 3] = ["unmet_dependencies", "dependencies", "blocks"];

/// Explicit, transactional native model v1 -> v2 upgrade. Foreign keys are
/// disabled only during the owned table rebuild and checked before commit.
/// Reject customized tables/indexes/triggers rather than silently discarding them.
pub fn upgrade_idea_schema(connection: &mut Connection, prefix: &str) -> Result<(), String> {
    if prefix.is_empty() || !prefix.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return Err("invalid table prefix".into());
    }
    if !connection.is_autocommit() {
        return Err("idea schema upgrade requires no active transaction".into());
    }
    let enforced: bool = connection.query_row("PRAGMA foreign_keys", [], |r| r.get(0)).map_err(describe)?;
    if !enforced { return Err("idea schema upgrade requires foreign-key enforcement".into()); }
    connection.pragma_update(None, "foreign_keys", "OFF").map_err(describe)?;
    let result = (|| {
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate).map_err(describe)?;
        let versions = transaction.prepare(&format!("SELECT version FROM {prefix}schema")).map_err(describe)?
            .query_map([], |r| r.get::<_, i64>(0)).map_err(describe)?
            .collect::<Result<Vec<_>, _>>().map_err(describe)?;
        if versions == [2] { return Ok(()); }
        if versions != [1] { return Err("unsupported model schema for idea upgrade".into()); }
        let original = Connection::open_in_memory().map_err(describe)?;
        original.execute_batch(&crate::MIGRATION.replace("frog_model_", prefix)).map_err(describe)?;
        for table in [format!("{prefix}tasks"), format!("{prefix}schema")] {
            let schema = |db: &Connection| -> Result<Vec<(String, String, Option<String>)>, String> {
                db.prepare("SELECT type,name,sql FROM sqlite_schema WHERE tbl_name=?1 ORDER BY type,name").map_err(describe)?
                    .query_map([&table], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?))).map_err(describe)?
                    .collect::<Result<Vec<_>, _>>().map_err(describe)
            };
            if schema(&transaction)? != schema(&original)? {
                return Err("customized model task/schema objects require an explicit upgrade adapter".into());
            }
        }
        transaction.execute_batch(&crate::IDEA_MIGRATION.replace("frog_model_", prefix)).map_err(describe)?;
        let invalid = transaction.prepare("PRAGMA foreign_key_check").map_err(describe)?
            .query_map([], |_| Ok(())).map_err(describe)?.next().is_some();
        if invalid { return Err("idea schema upgrade failed foreign-key verification".into()); }
        transaction.commit().map_err(describe)
    })();
    let restored = connection.pragma_update(None, "foreign_keys", "ON").map_err(describe);
    result.and(restored)
}

fn decode(name: &str, value: SqlValue) -> Result<Value, String> {
    let text = match value {
        SqlValue::Null => return Ok(Value::Null),
        SqlValue::Text(text) => text,
        other => return Ok(plain(other)),
    };
    if !JSON_LIST_COLUMNS.contains(&name) {
        return Ok(Value::String(text));
    }
    let list: Vec<String> =
        serde_json::from_str(&text).map_err(|_| "dependency projection was not a JSON list")?;
    Ok(Value::Array(list.into_iter().map(Value::String).collect()))
}
fn plain(value: SqlValue) -> Value {
    match value {
        SqlValue::Null => Value::Null,
        SqlValue::Integer(number) => Value::from(number),
        SqlValue::Real(number) => Value::from(number),
        SqlValue::Text(text) => Value::String(text),
        SqlValue::Blob(_) => Value::Null,
    }
}

pub fn execute(connection: &mut Connection, request: crate::Request) -> Result<Value, String> {
    execute_with_prefix(connection, request, "frog_model_")
}

pub fn execute_with_prefix(connection: &mut Connection, request: crate::Request, prefix: &str) -> Result<Value, String> {
    if prefix.is_empty() || !prefix.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return Err("invalid table prefix".into());
    }
    let needs_idea_schema = matches!(&request.command,
        crate::Command::ProviderImport { status, .. } | crate::Command::TransitionTask { status, .. } if status == "idea")
        || matches!(&request.command, crate::Command::CreateTask { initial_status: Some(status), .. } if status == "idea");
    let plan = crate::plan(request)?;
    let foreign_keys: i64 = connection
        .query_row("PRAGMA foreign_keys", [], |row| row.get(0))
        .map_err(|_| "database unavailable")?;
    if foreign_keys != 1 {
        return Err("foreign-key enforcement is required by this SQL adapter".into());
    }
    let transaction = connection
        .transaction_with_behavior(if plan.read_only {
            TransactionBehavior::Deferred
        } else {
            TransactionBehavior::Immediate
        })
        .map_err(|_| "transaction unavailable")?;
    let version: i64 = transaction
         .query_row(&format!("SELECT version FROM {prefix}schema"), [], |row| {
            row.get(0)
        })
        .map_err(|_| "model schema unavailable")?;
    if version != 1 && version != 2 {
        return Err("unsupported model schema".into());
    }
    if needs_idea_schema && version != 2 {
        return Err("idea requires explicit model schema v2 upgrade".into());
    }
    let mut result = Vec::new();
    for (index, item) in plan.statements.iter().enumerate() {
        let params: Vec<SqlValue> = item
            .params
            .iter()
            .map(|value| match value {
                Value::Null => Ok(SqlValue::Null),
                Value::String(text) => Ok(SqlValue::Text(text.clone())),
                Value::Number(number) => number
                    .as_i64()
                    .map(SqlValue::Integer)
                    .ok_or("unsupported number"),
                _ => Err("unsupported parameter"),
            })
            .collect::<Result<_, _>>()?;
        let sql = item.sql.replace("frog_model_", prefix);
        let mut statement = transaction.prepare(&sql).map_err(describe)?;
        if index + 1 == plan.statements.len() {
            let names: Vec<String> = statement
                .column_names()
                .iter()
                .map(|name| (*name).to_string())
                .collect();
            let mut rows = statement
                .query(rusqlite::params_from_iter(params))
                .map_err(|_| "query failed")?;
            while let Some(row) = rows.next().map_err(|_| "row read failed")? {
                let mut record = Map::new();
                for (column, name) in names.iter().enumerate() {
                    let value: SqlValue = row.get(column).map_err(|_| "column read failed")?;
                    record.insert(name.clone(), decode(name, value)?);
                }
                result.push(Value::Object(record));
            }
        } else {
            statement
                .execute(rusqlite::params_from_iter(params))
                .map_err(describe)?;
        }
    }
    transaction.commit().map_err(|_| "commit failed")?;
    Ok(serde_json::json!({"schema":"frog.model-result.v1","kind":plan.result_kind,"items":result}))
}

#[cfg(test)]
mod prefix_tests {
    use super::*;
    #[test]
    fn custom_prefix_executes_results_not_plans() {
        let mut conn = Connection::open_in_memory().unwrap();
        conn.pragma_update(None, "foreign_keys", "ON").unwrap();
        conn.execute_batch(&crate::MIGRATION.replace("frog_model_", "pilot_")).unwrap();
        let request = serde_json::from_value(serde_json::json!({
            "workspace_id":"test", "actor":"test", "operation_id":"read",
            "command":{"kind":"repositories","limit":10}
        })).unwrap();
        let result = execute_with_prefix(&mut conn, request, "pilot_").unwrap();
        assert_eq!(result["schema"], "frog.model-result.v1");
        assert!(result["items"].is_array());
        assert!(result.get("statements").is_none());
    }
}
