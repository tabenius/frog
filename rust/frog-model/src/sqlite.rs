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
    execute_fenced(connection, request, prefix, None, false)
}

/// Native runner evidence and exact lease are checked within the write transaction.
pub struct FinishFence<'a> {
    pub lease_id: &'a str,
    pub build_operation: &'a str,
    pub source_identity: &'a str,
}

pub fn execute_verified_finish(connection: &mut Connection, request: crate::Request, prefix: &str, fence: FinishFence<'_>) -> Result<Value, String> {
    execute_fenced(connection, request, prefix, Some(fence), false)
}

pub fn execute_exclusive_claim(connection: &mut Connection, request: crate::Request, prefix: &str) -> Result<Value, String> {
    if !matches!(&request.command, crate::Command::ClaimTask { .. }) { return Err("claim required".into()); }
    execute_fenced(connection, request, prefix, None, true)
}

fn execute_fenced(connection: &mut Connection, request: crate::Request, prefix: &str, fence: Option<FinishFence<'_>>, enforce_claim: bool) -> Result<Value, String> {
    if prefix.is_empty() || !prefix.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return Err("invalid table prefix".into());
    }
    let needs_idea_schema = matches!(&request.command,
        crate::Command::ProviderImport { status, .. } | crate::Command::TransitionTask { status, .. } if status == "idea")
        || matches!(&request.command, crate::Command::CreateTask { initial_status: Some(status), .. } if status == "idea");
    let finish = if fence.is_some() {
        match &request.command {
            crate::Command::TransitionTask { task_id, expected_revision, status } if status == "done" =>
                Some((request.workspace_id.clone(), request.actor.clone(), task_id.clone(), *expected_revision)),
            _ => return Err("finish fence requires completion".into()),
        }
    } else { None };
    let claim = if enforce_claim {
        if let crate::Command::ClaimTask { task_id, .. } = &request.command {
            Some((request.workspace_id.clone(), request.actor.clone(), task_id.clone()))
        } else { None }
    } else { None };
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
    if let Some((workspace, actor, task)) = &claim {
        // The client acquires the destination repo lease before claiming. A
        // fresh lease for this task must not reactivate explicitly parked work
        // in that same repo. Unparked assignments still block in every repo.
        let blocked: bool = transaction.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}tasks t WHERE t.workspace_id=?1 AND t.assignee=?2 AND t.task_id!=?3 AND t.status IN ('in_progress','review','published') AND (NOT EXISTS(SELECT 1 FROM {prefix}task_pauses p WHERE p.workspace_id=t.workspace_id AND p.task_id=t.task_id AND p.revision=t.revision AND p.actor=?2) OR (t.repo_id!=(SELECT repo_id FROM {prefix}tasks WHERE workspace_id=?1 AND task_id=?3) AND EXISTS(SELECT 1 FROM {prefix}locks l WHERE l.workspace_id=t.workspace_id AND l.scope=t.repo_id AND l.owner=?2 AND l.expires_at>unixepoch()))))"), rusqlite::params![workspace, actor, task], |row| row.get(0)).map_err(describe)?;
        if blocked { return Err("explicit pause or handoff of current work is required".into()); }
    }
    if let (Some(fence), Some((workspace, actor, task, revision))) = (&fence, &finish) {
        let valid: bool = transaction.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}tasks t JOIN {prefix}locks l ON l.workspace_id=t.workspace_id AND l.scope=t.repo_id JOIN {prefix}build_runs b ON b.workspace_id=t.workspace_id AND b.task_id=t.task_id WHERE t.workspace_id=?1 AND t.task_id=?2 AND t.revision=?3 AND t.assignee=?4 AND t.status='published' AND l.owner=?4 AND l.lease_id=?5 AND l.expires_at>unixepoch() AND b.operation_id=?6 AND b.revision=t.revision AND b.target='verify' AND json_extract(b.result,'$.source_identity')=?7 AND json_extract(b.result,'$.items[0].passed')=1)"),
            rusqlite::params![workspace, task, revision, actor, fence.lease_id, fence.build_operation, fence.source_identity], |row| row.get(0)).map_err(describe)?;
        if !valid { return Err("completion requires current runner evidence and exact live lease".into()); }
        let fresh: bool = transaction.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}verifications WHERE workspace_id=?1 AND task_id=?2 AND revision=?3 AND target='verify' AND passed=1 AND checked_at>=strftime('%Y-%m-%dT%H:%M:%fZ','now','-5 minutes'))"), rusqlite::params![workspace, task, revision], |row| row.get(0)).map_err(describe)?;
        if !fresh { return Err("completion verification expired".into()); }
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
    if let (Some(fence), Some((workspace, actor, _, _))) = (&fence, &finish) {
        if result.first().is_some_and(|row| row["status"] == "applied") {
            transaction.execute(&format!("DELETE FROM {prefix}locks WHERE workspace_id=?1 AND owner=?2 AND lease_id=?3"), rusqlite::params![workspace, actor, fence.lease_id]).map_err(describe)?;
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
