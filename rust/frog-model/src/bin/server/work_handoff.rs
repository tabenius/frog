//! Native-host parking protocol. All authority checks and writes share one transaction.
use super::{json, State, Value};
use rusqlite::{params, OptionalExtension, TransactionBehavior};
use sha2::{Digest, Sha256};

pub fn change(state: &State, workspace: &str, body: &Value) -> Result<Value, String> {
    if state.3.is_some() { return Err("work parking is unavailable in legacy mode".into()); }
    let field = |name: &str| body[name].as_str().filter(|value| !value.trim().is_empty()).ok_or_else(|| format!("{name} required"));
    let task = field("task_id")?;
    let operation = field("operation_id")?;
    let action = field("action")?;
    let lease = field("lease_id")?;
    let reason = field("reason")?;
    let receiver = body["receiver"].as_str();
    if !body["receiver"].is_null() && receiver.is_none_or(|value| value.trim().is_empty() || value.len() > 200) {
        return Err("invalid intended receiver".into());
    }
    if !matches!(action, "pause" | "handoff" | "resume") || reason.len() > 2000 || lease.len() > 128 || task.len() > 200 || operation.len() > 200 {
        return Err("invalid work parking request".into());
    }
    if action == "handoff" && receiver.is_none_or(|value| value.trim().is_empty() || value.len() > 200) {
        return Err("handoff requires an intended receiver".into());
    }
    if body.as_object().is_none_or(|value| value.keys().any(|key| !["task_id","operation_id","action","lease_id","reason","expected_revision","receiver"].contains(&key.as_str()))) {
        return Err("unknown work parking fields".into());
    }
    let revision = body["expected_revision"].as_u64().filter(|value| *value < u32::MAX as u64).ok_or("expected_revision required")? as u32;
    let key = format!("{:x}", Sha256::digest(serde_json::to_vec(body).map_err(|_| "invalid body")?));
    let prefix = &state.1;
    let actor = "local-client";
    let mut conn = state.0.lock().map_err(|_| "storage lock failed")?;
    let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate).map_err(|_| "transaction failed")?;
    let saved: Option<(String, u32)> = tx.query_row(&format!("SELECT request_key,result_revision FROM {prefix}work_handoffs WHERE workspace_id=?1 AND operation_id=?2"), params![workspace, operation], |row| Ok((row.get(0)?,row.get(1)?))).optional().map_err(|_| "audit lookup failed")?;
    if let Some((saved_key, result_revision)) = saved {
        if saved_key != key { return Err("operation ID reused with different handoff".into()); }
        return Ok(json!({"schema":"frog.model-result.v1","kind":"work_handoff","items":[{"status":"applied","task_id":task,"revision":result_revision,"action":action,"lease_id":lease}]}));
    }
    let reserved: bool = tx.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}operations WHERE workspace_id=?1 AND operation_id=?2) OR EXISTS(SELECT 1 FROM {prefix}build_runs WHERE workspace_id=?1 AND operation_id=?2)"), params![workspace,operation], |row|row.get(0)).map_err(|_| "operation lookup failed")?;
    if reserved { return Err("operation ID already used".into()); }
    let repo: String = tx.query_row(&format!("SELECT repo_id FROM {prefix}tasks WHERE workspace_id=?1 AND task_id=?2 AND revision=?3 AND assignee=?4 AND status IN ('in_progress','review','published')"), params![workspace, task, revision, actor], |row| row.get(0)).map_err(|_| "work owner, revision or state changed")?;
    if action == "resume" {
        let reused: bool = tx.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}work_handoffs WHERE workspace_id=?1 AND lease_id=?2)"), params![workspace,lease], |row| row.get(0)).map_err(|_| "lease history lookup failed")?;
        if reused { return Err("resume requires a fresh lease identity".into()); }
        let paused: bool = tx.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}task_pauses WHERE workspace_id=?1 AND task_id=?2 AND revision=?3 AND actor=?4)"), params![workspace,task,revision,actor], |row|row.get(0)).map_err(|_| "pause lookup failed")?;
        if !paused { return Err("resume requires an explicit current pause".into()); }
        let other: bool = tx.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}tasks t WHERE t.workspace_id=?1 AND t.assignee=?2 AND t.task_id!=?3 AND t.status IN ('in_progress','review','published') AND NOT EXISTS(SELECT 1 FROM {prefix}task_pauses p WHERE p.workspace_id=t.workspace_id AND p.task_id=t.task_id AND p.revision=t.revision AND p.actor=?2))"), params![workspace,actor,task], |row|row.get(0)).map_err(|_| "work lookup failed")?;
        if other { return Err("pause current work before resuming".into()); }
        let leased_other: bool = tx.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {prefix}tasks t JOIN {prefix}locks l ON l.workspace_id=t.workspace_id AND l.scope=t.repo_id WHERE t.workspace_id=?1 AND t.assignee=?2 AND t.task_id!=?3 AND t.status IN ('in_progress','review','published') AND l.owner=?2 AND l.expires_at>unixepoch())"), params![workspace,actor,task], |row|row.get(0)).map_err(|_| "active lease lookup failed")?;
        if leased_other { return Err("release other work leases before resuming".into()); }
        tx.execute(&format!("DELETE FROM {prefix}locks WHERE workspace_id=?1 AND scope=?2 AND expires_at<=unixepoch()"), params![workspace,repo]).map_err(|_| "expired lease cleanup failed")?;
        tx.execute(&format!("INSERT INTO {prefix}locks(workspace_id,scope,owner,lease_id,expires_at) VALUES(?1,?2,?3,?4,unixepoch()+3600)"), params![workspace,repo,actor,lease]).map_err(|_| "repository lease is held")?;
        tx.execute(&format!("DELETE FROM {prefix}task_pauses WHERE workspace_id=?1 AND task_id=?2"), params![workspace,task]).map_err(|_| "resume failed")?;
    } else {
        let released = tx.execute(&format!("DELETE FROM {prefix}locks WHERE workspace_id=?1 AND scope=?2 AND owner=?3 AND lease_id=?4 AND expires_at>unixepoch()"), params![workspace,repo,actor,lease]).map_err(|_| "lease lookup failed")?;
        if released != 1 { return Err("pause requires the exact live repository lease".into()); }
        tx.execute(&format!("INSERT INTO {prefix}task_pauses(workspace_id,task_id,revision,actor,reason,receiver) VALUES(?1,?2,?3,?4,?5,?6) ON CONFLICT(workspace_id,task_id) DO UPDATE SET revision=excluded.revision,actor=excluded.actor,reason=excluded.reason,receiver=excluded.receiver"), params![workspace,task,revision+1,actor,reason,receiver]).map_err(|_| "pause failed")?;
    }
    tx.execute(&format!("INSERT INTO {prefix}operations(workspace_id,operation_id,request_key,status) VALUES(?1,?2,?3,'applied')"), params![workspace,operation,key]).map_err(|_| "operation receipt failed")?;
    tx.execute(&format!("UPDATE {prefix}tasks SET revision=revision+1,last_operation_id=?3,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?1 AND task_id=?2"), params![workspace,task,operation]).map_err(|_| "task revision update failed")?;
    tx.execute(&format!("INSERT INTO {prefix}work_handoffs(workspace_id,operation_id,request_key,task_id,actor,kind,observed_revision,result_revision,lease_id,reason,receiver) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)"), params![workspace,operation,key,task,actor,action,revision,revision+1,lease,reason,receiver]).map_err(|_| "handoff audit failed")?;
    tx.execute(&format!("INSERT INTO {prefix}events(workspace_id,operation_id,entity_id,kind,entity_kind,sequence) SELECT ?1,?2,?3,?4,'task',COALESCE(MAX(sequence),0)+1 FROM {prefix}events WHERE workspace_id=?1"), params![workspace,operation,task,format!("task.{action}")]).map_err(|_| "handoff event failed")?;
    tx.commit().map_err(|_| "handoff commit failed")?;
    Ok(json!({"schema":"frog.model-result.v1","kind":"work_handoff","items":[{"status":"applied","task_id":task,"revision":revision+1,"action":action,"lease_id":lease}]}))
}
