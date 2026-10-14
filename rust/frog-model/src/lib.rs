//! The model produces trusted plans, not arbitrary SQL execution or authorization.
//! Native SQLite and the D1 host execute the same mutation plan atomically.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub const CONTRACT: &str = "frog.model.v1";
pub const MIGRATION: &str = include_str!("../sql/0001_model.sql");

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub workspace_id: String,
    pub actor: String,
    pub operation_id: String,
    pub command: Command,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    RegisterRepo {
        repo_id: String,
        title: String,
    },
    CreateTask {
        repo_id: String,
        task_id: String,
        title: String,
        priority: u8,
    },
    ClaimTask {
        task_id: String,
        expected_revision: u32,
    },
    TransitionTask {
        task_id: String,
        expected_revision: u32,
        status: String,
    },
    Board {
        repo_id: Option<String>,
        limit: u32,
    },
    Repositories {
        limit: u32,
    },
    Events {
        after: String,
        limit: u32,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Statement {
    pub sql: String,
    pub params: Vec<Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Plan {
    pub schema: String,
    pub read_only: bool,
    pub result_kind: String,
    pub statements: Vec<Statement>,
}

fn statement(sql: &str, params: Vec<Value>) -> Statement {
    Statement {
        sql: sql.into(),
        params,
    }
}
fn id(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 120
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
    {
        return Err("IDs must be 1–120 ASCII identifier characters".into());
    }
    Ok(())
}
fn title(value: &str) -> Result<(), String> {
    if value.trim().is_empty() || value.len() > 500 || value.chars().any(char::is_control) {
        return Err("invalid title".into());
    }
    Ok(())
}
fn limit(value: u32) -> Result<(), String> {
    if !(1..=200).contains(&value) {
        return Err("limit must be 1–200".into());
    }
    Ok(())
}

pub fn plan(request: Request) -> Result<Plan, String> {
    id(&request.workspace_id)?;
    id(&request.actor)?;
    id(&request.operation_id)?;
    let ws = json!(request.workspace_id);
    let actor = json!(request.actor);
    let op = json!(request.operation_id);
    let mut statements = Vec::new();
    let mut read_only = false;
    let result_kind;
    match &request.command {
        Command::Board {
            repo_id,
            limit: count,
        } => {
            limit(*count)?;
            if let Some(repo) = repo_id {
                id(repo)?;
            }
            read_only = true;
            result_kind = "board";
            statements.push(statement("SELECT t.task_id AS id,t.repo_id AS repository,t.title,t.priority,t.status,t.assignee,t.revision,t.updated_at,CASE WHEN t.status IN ('done','cancelled') THEN 'done' WHEN t.status IN ('in_progress','review','published') THEN 'in_progress' WHEN EXISTS (SELECT 1 FROM frog_model_dependencies d LEFT JOIN frog_model_tasks p ON p.workspace_id=d.workspace_id AND p.task_id=d.depends_on WHERE d.workspace_id=t.workspace_id AND d.task_id=t.task_id AND (p.status IS NULL OR p.status!='done')) THEN 'blocked' ELSE 'idea' END AS lane FROM frog_model_tasks t WHERE t.workspace_id=?1 AND (?2 IS NULL OR t.repo_id=?2) ORDER BY t.priority,t.task_id LIMIT ?3", vec![ws.clone(), json!(repo_id), json!(count)]));
        }
        Command::Repositories { limit: count } => {
            limit(*count)?;
            read_only = true;
            result_kind = "repositories";
            statements.push(statement("SELECT repo_id AS id,title FROM frog_model_repos WHERE workspace_id=? ORDER BY repo_id LIMIT ?", vec![ws.clone(), json!(count)]));
        }
        Command::Events {
            after,
            limit: count,
        } => {
            limit(*count)?;
            if after.is_empty()
                || after.len() > 19
                || !after.bytes().all(|b| b.is_ascii_digit())
                || after.parse::<i64>().is_err()
            {
                return Err("invalid event cursor".into());
            }
            read_only = true;
            result_kind = "events";
            statements.push(statement("SELECT CAST(sequence AS TEXT) AS cursor,operation_id,entity_kind,entity_id,kind,created_at FROM frog_model_events WHERE workspace_id=? AND sequence>CAST(? AS INTEGER) ORDER BY sequence LIMIT ?", vec![ws.clone(), json!(after), json!(count)]));
        }
        _ => {
            result_kind = "mutation";
            let request_key = format!(
                "{:x}",
                Sha256::digest(
                    serde_json::to_vec(&request).map_err(|_| "request serialization failed")?
                )
            );
            let key = json!(request_key);
            statements.push(statement("INSERT INTO frog_model_operations(workspace_id,operation_id,request_key,status) VALUES(?1,?2,?3,'pending') ON CONFLICT(workspace_id,operation_id) DO NOTHING", vec![ws.clone(),op.clone(),key.clone()]));
            let (table, target_column, target, event_kind) = match &request.command {
                Command::RegisterRepo {
                    repo_id,
                    title: label,
                } => {
                    id(repo_id)?;
                    title(label)?;
                    statements.push(statement("INSERT INTO frog_model_repos(workspace_id,repo_id,title,last_operation_id) SELECT ?1,?2,?3,?4 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?4 AND request_key=?5 AND status='pending') ON CONFLICT(workspace_id,repo_id) DO NOTHING", vec![ws.clone(),json!(repo_id),json!(label),op.clone(),key.clone()]));
                    ("frog_model_repos", "repo_id", repo_id, "repo.registered")
                }
                Command::CreateTask {
                    repo_id,
                    task_id,
                    title: label,
                    priority,
                } => {
                    id(repo_id)?;
                    id(task_id)?;
                    title(label)?;
                    if *priority > 3 {
                        return Err("priority must be 0–3".into());
                    }
                    statements.push(statement("INSERT INTO frog_model_tasks(workspace_id,task_id,repo_id,title,priority,status,revision,last_operation_id) SELECT ?1,?2,?3,?4,?5,'todo',0,?6 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?6 AND request_key=?7 AND status='pending') AND EXISTS(SELECT 1 FROM frog_model_repos WHERE workspace_id=?1 AND repo_id=?3) ON CONFLICT(workspace_id,task_id) DO NOTHING", vec![ws.clone(),json!(task_id),json!(repo_id),json!(label),json!(priority),op.clone(),key.clone()]));
                    ("frog_model_tasks", "task_id", task_id, "task.created")
                }
                Command::ClaimTask {
                    task_id,
                    expected_revision,
                } => {
                    id(task_id)?;
                    statements.push(statement("UPDATE frog_model_tasks SET status='in_progress',assignee=?1,revision=revision+1,last_operation_id=?2,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?3 AND task_id=?4 AND revision=?5 AND status='todo' AND assignee IS NULL AND EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?3 AND operation_id=?2 AND request_key=?6 AND status='pending') AND NOT EXISTS(SELECT 1 FROM frog_model_dependencies d LEFT JOIN frog_model_tasks p ON p.workspace_id=d.workspace_id AND p.task_id=d.depends_on WHERE d.workspace_id=?3 AND d.task_id=?4 AND (p.status IS NULL OR p.status!='done'))", vec![actor.clone(),op.clone(),ws.clone(),json!(task_id),json!(expected_revision),key.clone()]));
                    ("frog_model_tasks", "task_id", task_id, "task.claimed")
                }
                Command::TransitionTask {
                    task_id,
                    expected_revision,
                    status,
                } => {
                    id(task_id)?;
                    let previous = match status.as_str() {
                        "review" => "in_progress",
                        "published" => "review",
                        "done" => "published",
                        _ => return Err("transition requires review, published or done".into()),
                    };
                    statements.push(statement("UPDATE frog_model_tasks SET status=?1,revision=revision+1,last_operation_id=?2,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?3 AND task_id=?4 AND revision=?5 AND assignee=?6 AND status=?7 AND EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?3 AND operation_id=?2 AND request_key=?8 AND status='pending')", vec![json!(status),op.clone(),ws.clone(),json!(task_id),json!(expected_revision),actor.clone(),json!(previous),key.clone()]));
                    ("frog_model_tasks", "task_id", task_id, "task.transitioned")
                }
                _ => unreachable!(),
            };
            // Only internal identifiers enter SQL construction; all caller data is bound.
            let success = format!("EXISTS(SELECT 1 FROM {table} WHERE workspace_id=?1 AND {target_column}=?4 AND last_operation_id=?2)");
            let entity_kind = if target_column == "repo_id" {
                "repository"
            } else {
                "task"
            };
            statements.push(statement(&format!("INSERT INTO frog_model_events(workspace_id,operation_id,entity_id,kind,entity_kind,sequence) SELECT ?1,?2,?4,?5,?6,COALESCE((SELECT MAX(sequence) FROM frog_model_events WHERE workspace_id=?1),0)+1 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending') AND {success}"),vec![ws.clone(),op.clone(),key.clone(),json!(target),json!(event_kind),json!(entity_kind)]));
            statements.push(statement(&format!("UPDATE frog_model_operations SET status=CASE WHEN {success} THEN 'applied' ELSE 'conflict' END WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending'"),vec![ws.clone(),op.clone(),key.clone(),json!(target)]));
            statements.push(statement("SELECT operation_id,CASE WHEN request_key=?3 THEN status ELSE 'idempotency_conflict' END AS status FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2",vec![ws,op,key]));
        }
    }
    Ok(Plan {
        schema: CONTRACT.into(),
        read_only,
        result_kind: result_kind.into(),
        statements,
    })
}

#[cfg(feature = "component")]
mod component;
#[cfg(all(target_arch = "wasm32", not(feature = "component")))]
mod core_wasm;
#[cfg(feature = "sqlite")]
pub mod sqlite;
