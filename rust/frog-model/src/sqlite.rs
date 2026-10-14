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
        .query_row("SELECT version FROM frog_model_schema", [], |row| {
            row.get(0)
        })
        .map_err(|_| "model schema unavailable")?;
    if version != 1 {
        return Err("unsupported model schema".into());
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
        let mut statement = transaction.prepare(&item.sql).map_err(describe)?;
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
