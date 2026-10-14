//! Live legacy AGENTS.db adapter. No task copying or implicit persistent migration.
//! Legacy paths are mapped to repository identities at this storage boundary.
use rusqlite::{Connection, TransactionBehavior};
use serde_json::{json, Value};
use crate::{Command, Request};

fn prefix_valid(prefix: &str) -> Result<(), String> {
    if prefix.is_empty() || !prefix.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
        return Err("invalid table prefix".into());
    }
    Ok(())
}

/// Install connection-local views over the real tables. They disappear on close.
/// One legacy DB belongs to exactly one configured workspace, never every tenant.
pub fn prepare(connection: &Connection, workspace: &str, prefix: &str) -> Result<(), String> {
    prefix_valid(prefix)?;
    crate::plan(Request { workspace_id: workspace.into(), actor: "adapter".into(),
        operation_id: "validate".into(), command: Command::Repositories { limit: 1 } })?;
    // Validate all required columns before installing any views.
    for sql in [
        "SELECT slug,repo_path,title,priority,workflow_status,assigned_agent,created_at,updated_at,status_confidence_at FROM tasks LIMIT 0",
        "SELECT repo_path,name,repo_key FROM repos LIMIT 0",
        "SELECT task_slug,depends_on_slug,relation FROM task_dependencies LIMIT 0",
        "SELECT id,created_at,kind,task_slug,actor,payload_json FROM event_log LIMIT 0",
    ] { connection.prepare(sql).map_err(|e| format!("unsupported legacy schema: {e}"))?; }
    let unknown: i64 = connection.query_row(
        "SELECT count(*) FROM tasks WHERE priority NOT IN ('p0','p1','p2','p3') OR workflow_status NOT IN ('idea','todo','blocked','in_progress','review','published','done','cancelled')",
        [], |r| r.get(0)).map_err(|e| e.to_string())?;
    if unknown != 0 { return Err("legacy tasks contain unsupported priorities or statuses".into()); }
    // Validated identifier grammar excludes quotes, so workspace is safe here.
    let sql = format!(r#"
        CREATE TEMP VIEW {prefix}schema AS SELECT 1 AS version;
        CREATE TEMP VIEW {prefix}repos AS
          SELECT '{workspace}' AS workspace_id,
            COALESCE(NULLIF(repo_key,''),name) AS repo_id, name AS title FROM main.repos;
        CREATE TEMP VIEW {prefix}tasks AS
          SELECT '{workspace}' AS workspace_id,t.slug AS task_id,
            COALESCE(NULLIF(r.repo_key,''),r.name) AS repo_id,t.title,
            CAST(substr(t.priority,2) AS INTEGER) AS priority,
            t.workflow_status AS status,t.assigned_agent AS assignee,
            0 AS revision,t.updated_at,t.status_confidence_at AS status_observed_at,
            t.created_at FROM main.tasks t LEFT JOIN main.repos r ON r.repo_path=t.repo_path;
        CREATE TEMP VIEW {prefix}dependencies AS
          SELECT '{workspace}' AS workspace_id,task_slug AS task_id,depends_on_slug AS depends_on
          FROM main.task_dependencies WHERE relation='depends_on';
        CREATE TEMP VIEW {prefix}events AS
          SELECT '{workspace}' AS workspace_id,id AS sequence,CAST(id AS TEXT) AS operation_id,
            CASE WHEN task_slug IS NULL THEN 'repository' ELSE 'task' END AS entity_kind,
            COALESCE(task_slug,'') AS entity_id,kind,created_at FROM main.event_log;
    "#);
    connection.execute_batch(&sql).map_err(|e| e.to_string())
}

/// Explicit metadata setup, on a backed-up DB only. Receipts share the mutation
/// transaction with legacy writes. No existing legacy schema or rows are changed.
pub fn initialise_receipts(connection: &mut Connection, prefix: &str) -> Result<(), String> {
    prefix_valid(prefix)?;
    let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|e| e.to_string())?;
    tx.execute_batch(&format!("CREATE TABLE IF NOT EXISTS {prefix}legacy_receipts (
        workspace_id TEXT NOT NULL, operation_id TEXT NOT NULL, request_key TEXT NOT NULL,
        result_json TEXT NOT NULL, PRIMARY KEY(workspace_id,operation_id));")).map_err(|e| e.to_string())?;
    tx.commit().map_err(|e| e.to_string())
}

pub fn execute(connection: &mut Connection, request: Request, workspace: &str, prefix: &str) -> Result<Value, String> {
    prefix_valid(prefix)?;
    if request.workspace_id != workspace { return Err("unknown workspace".into()); }
    let plan = crate::plan(request.clone())?;
    if plan.read_only { return crate::sqlite::execute_with_prefix(connection, request, prefix); }
    // Legacy claim/finish also manage locks, assignments, verification and status
    // history. A fabricated revision would make optimistic concurrency dishonest.
    if !matches!(request.command, Command::CreateTask {..} | Command::AddDependency {..} | Command::RemoveDependency {..}) {
        return Err("unsupported legacy operation: requires lock/revision compatibility; use legacy CLI explicitly".into());
    }
    use sha2::{Digest, Sha256};
    let key = format!("{:x}", Sha256::digest(serde_json::to_vec(&request).map_err(|e| e.to_string())?));
    let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|e| e.to_string())?;
    let receipt = tx.query_row(&format!("SELECT request_key,result_json FROM {prefix}legacy_receipts WHERE workspace_id=?1 AND operation_id=?2"),
        [&request.workspace_id, &request.operation_id], |r| Ok((r.get::<_,String>(0)?,r.get::<_,String>(1)?)));
    match receipt {
        Ok((old_key,result)) => return if old_key == key { serde_json::from_str(&result).map_err(|e| e.to_string()) }
            else { Ok(json!({"schema":"frog.model-result.v1","kind":"mutation","items":[{"operation_id":request.operation_id,"status":"idempotency_conflict"}]})) },
        Err(rusqlite::Error::QueryReturnedNoRows) => (),
        Err(e) => return Err(format!("legacy receipts unavailable (initialise explicitly): {e}")),
    }
    let (changed, entity, event) = match &request.command {
        Command::CreateTask { repo_id, task_id, title, priority } => {
            let count: i64 = tx.query_row("SELECT count(*) FROM repos WHERE COALESCE(NULLIF(repo_key,''),name)=?1",[repo_id],|r|r.get(0)).map_err(|e|e.to_string())?;
            if count != 1 { return Err("repository identity is missing or ambiguous".into()); }
            let changed = tx.execute("INSERT INTO tasks(slug,repo_path,title,priority,workflow_status,git_status,created_at,updated_at,status_confidence_at)
                SELECT ?1,repo_path,?2,?3,'todo','not_started',strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now'),strftime('%Y-%m-%dT%H:%M:%fZ','now')
                FROM repos WHERE COALESCE(NULLIF(repo_key,''),name)=?4 ON CONFLICT(slug) DO NOTHING",
                rusqlite::params![task_id,title,format!("p{priority}"),repo_id]).map_err(|e|e.to_string())?;
            (changed,*&task_id,"task.created")
        },
        Command::AddDependency {task_id, depends_on} => {
            let cycle: bool = tx.query_row("WITH RECURSIVE ancestors(slug) AS (SELECT ?1 UNION SELECT d.depends_on_slug FROM task_dependencies d JOIN ancestors a ON d.task_slug=a.slug WHERE d.relation='depends_on') SELECT EXISTS(SELECT 1 FROM ancestors WHERE slug=?2)",
                [depends_on,task_id],|r|r.get(0)).map_err(|e|e.to_string())?;
            if cycle { return Err("dependency would create a cycle".into()); }
            let changed = tx.execute("INSERT INTO task_dependencies(task_slug,depends_on_slug,relation,created_at) VALUES(?1,?2,'depends_on',strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT DO NOTHING",
                [task_id,depends_on]).map_err(|e|e.to_string())?;
            (changed,task_id,"task.dependency_added")
        },
        Command::RemoveDependency {task_id,depends_on} => {
            let changed = tx.execute("DELETE FROM task_dependencies WHERE task_slug=?1 AND depends_on_slug=?2 AND relation='depends_on'",[task_id,depends_on]).map_err(|e|e.to_string())?;
            (changed,task_id,"task.dependency_removed")
        },
        _ => unreachable!(),
    };
    let status = if changed == 0 && matches!(request.command,Command::CreateTask {..}) {"conflict"} else {"applied"};
    if changed > 0 {
        tx.execute("INSERT INTO event_log(created_at,kind,task_slug,actor,summary,payload_json) VALUES(strftime('%Y-%m-%dT%H:%M:%fZ','now'),?1,?2,?3,?1,?4)",
            rusqlite::params![event,entity,request.actor,json!({"operation_id":request.operation_id,"source":"frog-model-legacy"}).to_string()]).map_err(|e|e.to_string())?;
    }
    let result = json!({"schema":"frog.model-result.v1","kind":"mutation","items":[{"operation_id":request.operation_id,"status":status}]});
    tx.execute(&format!("INSERT INTO {prefix}legacy_receipts VALUES(?1,?2,?3,?4)"),rusqlite::params![workspace,request.operation_id,key,result.to_string()]).map_err(|e|e.to_string())?;
    tx.commit().map_err(|e|e.to_string())?;
    Ok(result)
}
