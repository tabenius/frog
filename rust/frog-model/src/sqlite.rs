//! Native adapter: ordinary SQLite transactions; WAL is optional, not a contract.
use rusqlite::{types::Value as SqlValue, Connection, TransactionBehavior};
use serde_json::{Map, Value};

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
        let mut statement = transaction
            .prepare(&item.sql)
            .map_err(|_| "statement preparation failed")?;
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
                    let value = match value {
                        SqlValue::Null => Value::Null,
                        SqlValue::Integer(number) => Value::from(number),
                        SqlValue::Text(text) => Value::String(text),
                        _ => return Err("unsupported result value".into()),
                    };
                    record.insert(name.clone(), value);
                }
                result.push(Value::Object(record));
            }
        } else {
            statement
                .execute(rusqlite::params_from_iter(params))
                .map_err(|_| "atomic operation failed")?;
        }
    }
    transaction.commit().map_err(|_| "commit failed")?;
    Ok(serde_json::json!({"schema":"frog.model-result.v1","kind":plan.result_kind,"items":result}))
}
