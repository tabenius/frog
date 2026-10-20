//! The model produces trusted plans, not arbitrary SQL execution or authorization.
//! Native SQLite and the D1 host execute the same mutation plan atomically.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub mod data_format;

pub const CONTRACT: &str = "frog.model.v1";
pub const MIGRATION: &str = include_str!("../sql/0001_model.sql");
pub const SERVICE_MIGRATION: &str = include_str!("../sql/0002_services.sql");
/// Priorities the model accepts; Frog's legacy vocabulary is a host concern.
const PRIORITIES: [u8; 4] = [0, 1, 2, 3];
/// Report the next workflow state; never a leap, never out of reported order.
const TRANSITIONS: [(&str, &str); 3] = [
    ("review", "in_progress"),
    ("published", "review"),
    ("done", "published"),
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub workspace_id: String,
    pub actor: String,
    pub operation_id: String,
    pub command: Command,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TaskEditFields {
    pub title: Option<String>,
    pub priority: Option<u8>,
    pub repo_id: Option<String>,
    pub why: Option<String>,
    pub what_text: Option<String>,
    pub roi_note: Option<String>,
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
    AddDependency {
        task_id: String,
        depends_on: String,
    },
    RemoveDependency {
        task_id: String,
        depends_on: String,
    },
    /// Block the task once. Unblocking is a remove_dependency, not a flag.
    ClaimTask {
        task_id: String,
        expected_revision: u32,
    },
    TransitionTask {
        task_id: String,
        expected_revision: u32,
        status: String,
    },
    CancelTask {
        task_id: String,
        expected_revision: u32,
    },
    EditTask {
        task_id: String,
        expected_revision: u32,
        title: Option<String>,
        priority: Option<u8>,
    },
    EditTaskFields {
        task_id: String,
        expected_revision: u32,
        fields: TaskEditFields,
    },
    TaskDetails {
        task_id: String,
    },
    ProviderImport {
        source: String,
        external_id: String,
        repo_id: String,
        task_id: String,
        title: String,
        priority: u8,
        status: String,
        expected_revision: Option<u32>,
        why: Option<String>,
        what_text: Option<String>,
    },
    ProviderOutbox {
        source: String,
        limit: u32,
        after: Option<String>,
    },
    RecordVerification {
        task_id: String,
        expected_revision: u32,
        target: String,
        passed: bool,
    },
    Verifications {
        task_id: String,
    },
    AcquireLock {
        scope: String,
        lease_id: String,
        ttl_seconds: u32,
    },
    ReleaseLock {
        scope: String,
        lease_id: String,
    },
    Locks {
        limit: u32,
        after: Option<String>,
    },
    DescribeTask {
        task_id: String,
    },
    Board {
        repo_id: Option<String>,
        limit: u32,
        after: Option<String>,
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
fn ok(read_only: bool, result_kind: &str, statements: Vec<Statement>) -> Result<Plan, String> {
    Ok(Plan {
        schema: CONTRACT.into(),
        read_only,
        result_kind: result_kind.into(),
        statements,
    })
}

fn id(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 120
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
    {
        return Err("IDs must be 1-120 ASCII identifier characters".into());
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
        return Err("limit must be 1-200".into());
    }
    Ok(())
}
fn count(value: &str) -> Result<u64, String> {
    if value.is_empty() || value.len() > 19 || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err("invalid event cursor".into());
    }
    value.parse().map_err(|_| "invalid event cursor".into())
}
/// Opaque, validated keyset cursor: "<priority>:<task_id>".
fn board_cursor(value: &str) -> Result<(u8, String), String> {
    let (priority, task) = value.split_once(':').ok_or("invalid board cursor")?;
    let priority: u8 = priority.parse().map_err(|_| "invalid board cursor")?;
    if !PRIORITIES.contains(&priority) {
        return Err("invalid board cursor".into());
    }
    id(task)?;
    Ok((priority, task.into()))
}

/// Board rows, including the dependency context the renderer links and explains.
const BOARD_SELECT: &str = "SELECT t.task_id AS id,t.repo_id AS repository,t.title,t.priority,t.status,t.assignee,\
t.revision,t.updated_at,t.status_observed_at,\
CASE WHEN t.status IN ('done','cancelled') THEN 'done' \
WHEN t.status IN ('in_progress','review','published') THEN 'in_progress' \
WHEN EXISTS (SELECT 1 FROM frog_model_dependencies d LEFT JOIN frog_model_tasks p \
  ON p.workspace_id=d.workspace_id AND p.task_id=d.depends_on \
  WHERE d.workspace_id=t.workspace_id AND d.task_id=t.task_id AND (p.status IS NULL OR p.status!='done')) \
THEN 'blocked' ELSE 'idea' END AS lane,\
(SELECT COALESCE(json_group_array(o.id),'[]') FROM (SELECT j.depends_on AS id FROM frog_model_dependencies j \
  LEFT JOIN frog_model_tasks p ON p.workspace_id=j.workspace_id AND p.task_id=j.depends_on \
  WHERE j.workspace_id=t.workspace_id AND j.task_id=t.task_id AND (p.status IS NULL OR p.status!='done') \
  ORDER BY j.depends_on) o) AS unmet_dependencies,\
(SELECT COALESCE(json_group_array(o.id),'[]') FROM (SELECT j.depends_on AS id FROM frog_model_dependencies j \
  WHERE j.workspace_id=t.workspace_id AND j.task_id=t.task_id ORDER BY j.depends_on) o) AS dependencies,\
(SELECT COALESCE(json_group_array(o.id),'[]') FROM (SELECT j.task_id AS id FROM frog_model_dependencies j \
  WHERE j.workspace_id=t.workspace_id AND j.depends_on=t.task_id ORDER BY j.task_id) o) AS blocks \
FROM frog_model_tasks t WHERE t.workspace_id=?1";

/// One mutation: idempotency receipt, conditional entity write, event, outcome
/// and result read, executed as a single host transaction.
///
/// Parameter positions are fixed: ?1 workspace, ?2 operation, ?3 request hash,
/// ?4 actor, ?5 effect identity, ?6 secondary identity, ?7+ command-specific
/// values, then event kind and entity kind at the end. Hosts bind each statement
/// with exactly as many values as it declares placeholders. `satisfied` decides
/// the outcome and whether anything is recorded; it may reference ?1-?6 only.
struct Mutation<'a> {
    entity: &'a str,
    effect: Value,
    secondary: Value,
    extras: Vec<Value>,
    /// Optional statement that marks the receipt `changed` when this operation
    /// really alters state. Needed where the entity write destroys the very
    /// evidence of that fact, such as deleting a dependency edge.
    precheck: Option<&'a str>,
    satisfied: &'a str,
    event: &'a str,
    entity_kind: &'a str,
}
const PENDING: &str =
    "EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 \
AND request_key=?3 AND status='pending')";
const CHANGED: &str =
    "EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND changed=1)";
const WROTE: &str = "EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?5 \
AND last_operation_id=?2)";
const EDGE: &str = "EXISTS(SELECT 1 FROM frog_model_dependencies WHERE workspace_id=?1 \
AND task_id=?5 AND depends_on=?6)";

/// Highest `?N` placeholder in a statement, ignoring quoted text. Hosts must
/// bind exactly this many values, so the bound vector is sliced per statement
/// rather than assumed.
fn placeholders(sql: &str) -> usize {
    let mut highest = 0;
    let mut quoted = false;
    let bytes = sql.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'\'' => quoted = !quoted,
            b'?' if !quoted => {
                let mut digits = String::new();
                while index + 1 + digits.len() < bytes.len()
                    && bytes[index + 1 + digits.len()].is_ascii_digit()
                {
                    digits.push(bytes[index + 1 + digits.len()] as char);
                }
                highest = highest.max(digits.parse().unwrap_or(0));
                index += digits.len();
            }
            _ => {}
        }
        index += 1;
    }
    highest
}
fn bound_statement(sql: &str, bound: &[Value]) -> Result<Statement, String> {
    let needed = placeholders(sql);
    if needed > bound.len() {
        return Err("plan binds fewer values than its statements require".into());
    }
    Ok(statement(sql, bound[..needed].to_vec()))
}

fn mutation(request: &Request, spec: Mutation<'_>) -> Result<Plan, String> {
    let key = json!(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(request).map_err(|_| "request serialization failed")?)
    ));
    let mut bound = vec![
        json!(request.workspace_id),
        json!(request.operation_id),
        key.clone(),
        json!(request.actor),
        spec.effect.clone(),
        spec.secondary.clone(),
    ];
    bound.extend(spec.extras);
    let event_at = bound.len() + 1;
    bound.push(json!(spec.event));
    bound.push(json!(spec.entity_kind));
    // Applied means the requested state holds; recorded means this operation is
    // what made it hold, so the event stream is a delta, not a repeat log.
    let outcome = format!("{PENDING} AND ({CHANGED} OR {})", spec.satisfied);
    let recorded = match spec.precheck {
        // With a precheck, only a recorded change may emit an event.
        Some(_) => CHANGED.to_string(),
        None => spec.satisfied.to_string(),
    };
    let receipt = "INSERT INTO frog_model_operations(workspace_id,operation_id,request_key,status) VALUES(?1,?2,?3,'pending') \
ON CONFLICT(workspace_id,operation_id) DO NOTHING";
    let mut statements = vec![bound_statement(receipt, &bound)?];
    if let Some(precheck) = spec.precheck {
        statements.push(bound_statement(precheck, &bound)?);
    }
    statements.extend([
        bound_statement(spec.entity, &bound)?,
        bound_statement(
            &format!("INSERT INTO frog_model_events(workspace_id,operation_id,entity_id,kind,entity_kind,sequence) \
SELECT ?1,?2,?5,?{event_at},?{},COALESCE((SELECT MAX(sequence) FROM frog_model_events WHERE workspace_id=?1),0)+1 \
WHERE {PENDING} AND {recorded}", event_at + 1),
            &bound,
        )?,
        bound_statement(
            &format!("UPDATE frog_model_operations SET status=CASE WHEN {outcome} THEN 'applied' ELSE 'conflict' END \
WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending'"),
            &bound,
        )?,
        bound_statement(
            "SELECT operation_id,CASE WHEN request_key=?3 THEN status ELSE 'idempotency_conflict' END AS status \
FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2",
            &bound,
        )?,
    ]);
    ok(false, "mutation", statements)
}

pub fn plan(request: Request) -> Result<Plan, String> {
    id(&request.workspace_id)?;
    id(&request.actor)?;
    id(&request.operation_id)?;
    let workspace = json!(request.workspace_id);
    match &request.command {
        Command::EditTaskFields {
            task_id,
            expected_revision,
            fields,
        } => {
            id(task_id)?;
            if let Some(label) = &fields.title {
                title(label)?;
            }
            if let Some(repo) = &fields.repo_id {
                id(repo)?;
            }
            if fields.priority.is_some_and(|p| !PRIORITIES.contains(&p)) {
                return Err("priority must be 0-3".into());
            }
            if fields.title.is_none()
                && fields.priority.is_none()
                && fields.repo_id.is_none()
                && fields.why.is_none()
                && fields.what_text.is_none()
                && fields.roi_note.is_none()
            {
                return Err("edit requires at least one field".into());
            }
            for value in [&fields.why, &fields.what_text, &fields.roi_note]
                .into_iter()
                .flatten()
            {
                if value.len() > 4096 {
                    return Err("task metadata exceeds 4096 bytes".into());
                }
            }
            let mut plan = mutation(&request,Mutation {
                entity:"UPDATE frog_model_tasks SET title=COALESCE(?7,title),priority=COALESCE(?8,priority),repo_id=COALESCE(?9,repo_id),revision=revision+1,last_operation_id=?2,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?1 AND task_id=?5 AND revision=?6 AND (?9 IS NULL OR EXISTS(SELECT 1 FROM frog_model_repos WHERE workspace_id=?1 AND repo_id=?9)) AND EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending')",
                effect:json!(task_id),secondary:json!(expected_revision),extras:vec![json!(fields.title),json!(fields.priority),json!(fields.repo_id),json!(fields.why),json!(fields.what_text),json!(fields.roi_note)],precheck:None,satisfied:WROTE,event:"task.edited",entity_kind:"task",
            })?;
            let details = bound_statement(&format!("INSERT INTO frog_model_task_details(workspace_id,task_id,why,what_text,roi_note) SELECT ?1,?5,?10,?11,?12 WHERE {PENDING} AND {WROTE} ON CONFLICT(workspace_id,task_id) DO UPDATE SET why=COALESCE(excluded.why,why),what_text=COALESCE(excluded.what_text,what_text),roi_note=COALESCE(excluded.roi_note,roi_note)"),&plan.statements[2].params)?;
            plan.statements.insert(2, details);
            Ok(plan)
        }
        Command::TaskDetails { task_id } => {
            id(task_id)?;
            ok(true,"task_details",vec![statement("SELECT why,what_text,roi_note FROM frog_model_task_details WHERE workspace_id=?1 AND task_id=?2",vec![workspace,json!(task_id)])])
        }
        Command::ProviderImport {
            source,
            external_id,
            repo_id,
            task_id,
            title: label,
            priority,
            status,
            expected_revision,
            why,
            what_text,
        } => {
            for value in [source, repo_id, task_id] {
                id(value)?;
            }
            title(external_id)?;
            title(label)?;
            for value in [why, what_text].into_iter().flatten() {
                if value.len() > 4096 {
                    return Err("provider metadata exceeds 4096 bytes".into());
                }
            }
            if !PRIORITIES.contains(priority)
                || ![
                    "todo",
                    "in_progress",
                    "review",
                    "published",
                    "done",
                    "cancelled",
                ]
                .contains(&status.as_str())
            {
                return Err("provider priority/status invalid".into());
            }
            let mut plan = mutation(&request, Mutation {
                entity:"INSERT INTO frog_model_tasks(workspace_id,task_id,repo_id,title,priority,status,revision,last_operation_id) SELECT ?1,?5,?7,?8,?9,?10,0,?2 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending') AND EXISTS(SELECT 1 FROM frog_model_provider_items WHERE workspace_id=?1 AND source=?11 AND external_id=?6 AND task_id=?5) AND ?12 IS NULL ON CONFLICT(workspace_id,task_id) DO NOTHING",
                effect:json!(task_id),secondary:json!(external_id),extras:vec![json!(repo_id),json!(label),json!(priority),json!(status),json!(source),json!(expected_revision),json!(why),json!(what_text)],
                precheck:Some("INSERT INTO frog_model_provider_items(workspace_id,source,external_id,task_id) SELECT ?1,?11,?6,?5 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending') AND ?12 IS NULL AND NOT EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?5) ON CONFLICT DO NOTHING"),
                satisfied:WROTE,event:"provider.imported",entity_kind:"task",
            })?;
            let update = bound_statement("UPDATE frog_model_tasks SET title=?8,priority=?9,status=?10,revision=revision+1,last_operation_id=?2,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),status_observed_at=CASE WHEN status!=?10 THEN strftime('%Y-%m-%dT%H:%M:%fZ','now') ELSE status_observed_at END WHERE workspace_id=?1 AND task_id=?5 AND repo_id=?7 AND revision=?12 AND assignee IS NULL AND EXISTS(SELECT 1 FROM frog_model_provider_items WHERE workspace_id=?1 AND source=?11 AND external_id=?6 AND task_id=?5) AND EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending')", &plan.statements[2].params)?;
            let details = bound_statement(&format!("INSERT INTO frog_model_task_details(workspace_id,task_id,why,what_text) SELECT ?1,?5,?13,?14 WHERE {PENDING} AND {WROTE} ON CONFLICT(workspace_id,task_id) DO UPDATE SET why=COALESCE(excluded.why,why),what_text=COALESCE(excluded.what_text,what_text)"), &plan.statements[3].params)?;
            plan.statements.insert(3, update);
            plan.statements.insert(4, details);
            // The mapping insert is not a change-marker precheck. Emit only
            // when this operation actually wrote the task.
            plan.statements[5].sql = plan.statements[5].sql.replace(CHANGED, WROTE);
            Ok(plan)
        }
        Command::ProviderOutbox {
            source,
            limit: size,
            after,
        } => {
            id(source)?;
            limit(*size)?;
            if let Some(cursor) = after {
                title(cursor)?;
            }
            ok(true,"provider_outbox",vec![statement("SELECT p.external_id,t.task_id AS id,t.title,t.status,t.priority,t.assignee,t.revision FROM frog_model_provider_items p JOIN frog_model_tasks t ON t.workspace_id=p.workspace_id AND t.task_id=p.task_id WHERE p.workspace_id=?1 AND p.source=?2 AND (?4 IS NULL OR p.external_id>?4) ORDER BY p.external_id LIMIT ?3",vec![workspace,json!(source),json!(size),json!(after)])])
        }
        Command::RecordVerification {
            task_id,
            expected_revision,
            target,
            passed,
        } => {
            id(task_id)?;
            id(target)?;
            mutation(&request,Mutation {
                entity:"INSERT INTO frog_model_verifications(workspace_id,task_id,revision,target,passed) SELECT ?1,?5,?6,?7,?8 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending') AND EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?5 AND revision=?6) ON CONFLICT(workspace_id,task_id,revision,target) DO UPDATE SET passed=excluded.passed,checked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                effect:json!(task_id),secondary:json!(expected_revision),extras:vec![json!(target),json!(u8::from(*passed))],precheck:None,
                satisfied:"EXISTS(SELECT 1 FROM frog_model_verifications WHERE workspace_id=?1 AND task_id=?5 AND revision=?6 AND target=?7 AND passed=?8) AND EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?5 AND revision=?6)",
                event:"task.verified",entity_kind:"task",
            })
        }
        Command::Verifications { task_id } => {
            id(task_id)?;
            ok(true,"verifications",vec![statement("SELECT task_id,revision,target,passed,checked_at FROM frog_model_verifications WHERE workspace_id=?1 AND task_id=?2 ORDER BY revision,target",vec![workspace,json!(task_id)])])
        }
        Command::AcquireLock {
            scope,
            lease_id,
            ttl_seconds,
        } => {
            id(scope)?;
            id(lease_id)?;
            if !(1..=3600).contains(ttl_seconds) {
                return Err("lock TTL must be 1-3600 seconds".into());
            }
            mutation(&request,Mutation {
                entity:"INSERT INTO frog_model_locks(workspace_id,scope,owner,lease_id,expires_at) SELECT ?1,?5,?4,?6,unixepoch()+?7 WHERE EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending') ON CONFLICT(workspace_id,scope) DO UPDATE SET owner=excluded.owner,lease_id=excluded.lease_id,expires_at=excluded.expires_at WHERE frog_model_locks.expires_at<=unixepoch() OR (frog_model_locks.owner=?4 AND frog_model_locks.lease_id=?6)",
                effect:json!(scope),secondary:json!(lease_id),extras:vec![json!(ttl_seconds)],precheck:None,
                satisfied:"EXISTS(SELECT 1 FROM frog_model_locks WHERE workspace_id=?1 AND scope=?5 AND owner=?4 AND lease_id=?6 AND expires_at>unixepoch())",
                event:"lock.acquired",entity_kind:"repository",
            })
        }
        Command::ReleaseLock { scope, lease_id } => {
            id(scope)?;
            id(lease_id)?;
            mutation(&request,Mutation {
                entity:"DELETE FROM frog_model_locks WHERE workspace_id=?1 AND scope=?5 AND owner=?4 AND lease_id=?6 AND EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending')",
                effect:json!(scope),secondary:json!(lease_id),extras:vec![],
                precheck:Some("UPDATE frog_model_operations SET changed=1 WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending' AND EXISTS(SELECT 1 FROM frog_model_locks WHERE workspace_id=?1 AND scope=?5 AND owner=?4 AND lease_id=?6)"),
                satisfied:"NOT EXISTS(SELECT 1 FROM frog_model_locks WHERE workspace_id=?1 AND scope=?5)",event:"lock.released",entity_kind:"repository",
            })
        }
        Command::Locks { limit: size, after } => {
            limit(*size)?;
            if let Some(cursor) = after {
                id(cursor)?;
            }
            ok(true,"locks",vec![statement("SELECT scope,owner,lease_id,expires_at FROM frog_model_locks WHERE workspace_id=?1 AND expires_at>unixepoch() AND (?3 IS NULL OR scope>?3) ORDER BY scope LIMIT ?2",vec![workspace,json!(size),json!(after)])])
        }
        Command::Board {
            repo_id,
            limit: size,
            after,
        } => {
            limit(*size)?;
            if let Some(repo) = repo_id {
                id(repo)?;
            }
            let (priority, task) = match after {
                Some(cursor) => board_cursor(cursor)?,
                None => (0, String::new()),
            };
            ok(true, "board", vec![statement(
                &format!("{BOARD_SELECT} AND (?2 IS NULL OR t.repo_id=?2) \
AND (?5=0 OR t.priority>?6 OR (t.priority=?6 AND t.task_id>?7)) ORDER BY t.priority,t.task_id LIMIT ?3"),
                vec![workspace, json!(repo_id), json!(size), Value::Null, json!(u8::from(after.is_some())), json!(priority), json!(task)],
            )])
        }
        Command::DescribeTask { task_id } => {
            id(task_id)?;
            ok(
                true,
                "task",
                vec![statement(
                    &format!("{BOARD_SELECT} AND t.task_id=?2"),
                    vec![workspace, json!(task_id)],
                )],
            )
        }
        Command::Repositories { limit: size } => {
            limit(*size)?;
            ok(true, "repositories", vec![statement(
                "SELECT repo_id AS id,title FROM frog_model_repos WHERE workspace_id=?1 ORDER BY repo_id LIMIT ?2",
                vec![workspace, json!(size)],
            )])
        }
        Command::Events { after, limit: size } => {
            limit(*size)?;
            count(after)?;
            ok(true, "events", vec![statement(
                "SELECT CAST(sequence AS TEXT) AS cursor,operation_id,entity_kind,entity_id,kind,created_at \
FROM frog_model_events WHERE workspace_id=?1 AND sequence>CAST(?2 AS INTEGER) ORDER BY sequence LIMIT ?3",
                vec![workspace, json!(after), json!(size)],
            )])
        }
        Command::RegisterRepo {
            repo_id,
            title: label,
        } => {
            id(repo_id)?;
            title(label)?;
            // Register is idempotent and refreshes the title: re-registering an
            // existing repository is an explicit act, not a silent overwrite.
            mutation(&request, Mutation {
                entity: "INSERT INTO frog_model_repos(workspace_id,repo_id,title,last_operation_id) \
SELECT ?1,?5,?7,?2 WHERE EXISTS(SELECT 1 FROM frog_model_workspaces WHERE workspace_id=?1) \
ON CONFLICT(workspace_id,repo_id) DO UPDATE SET title=excluded.title, last_operation_id=excluded.last_operation_id",
                effect: json!(repo_id), secondary: Value::Null, extras: vec![json!(label)],
                precheck: None,
                satisfied: "EXISTS(SELECT 1 FROM frog_model_repos WHERE workspace_id=?1 AND repo_id=?5 AND last_operation_id=?2)",
                event: "repo.registered", entity_kind: "repository",
            })
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
            if !PRIORITIES.contains(priority) {
                return Err("priority must be 0-3".into());
            }
            mutation(&request, Mutation {
                entity: "INSERT INTO frog_model_tasks(workspace_id,task_id,repo_id,title,priority,status,revision,last_operation_id,status_observed_at) \
SELECT ?1,?5,?6,?7,?8,'todo',0,?2,strftime('%Y-%m-%dT%H:%M:%fZ','now') \
WHERE EXISTS(SELECT 1 FROM frog_model_repos WHERE workspace_id=?1 AND repo_id=?6) \
ON CONFLICT(workspace_id,task_id) DO NOTHING",
                effect: json!(task_id), secondary: json!(repo_id), extras: vec![json!(label), json!(priority)],
                precheck: None, satisfied: WROTE, event: "task.created", entity_kind: "task",
            })
        }
        Command::AddDependency {
            task_id,
            depends_on,
        } => {
            id(task_id)?;
            id(depends_on)?;
            if task_id == depends_on {
                return Err("a task cannot depend on itself".into());
            }
            mutation(&request, Mutation {
                entity: "INSERT INTO frog_model_dependencies(workspace_id,task_id,depends_on) \
SELECT ?1,?5,?6 WHERE EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?5) \
AND EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?6) \
ON CONFLICT(workspace_id,task_id,depends_on) DO NOTHING",
                effect: json!(task_id), secondary: json!(depends_on), extras: Vec::new(),
                precheck: Some(&format!("UPDATE frog_model_operations SET changed=1 WHERE {PENDING} AND NOT {EDGE} \
AND EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?5) \
AND EXISTS(SELECT 1 FROM frog_model_tasks WHERE workspace_id=?1 AND task_id=?6)")),
                satisfied: EDGE, event: "task.dependency_added", entity_kind: "task",
            })
        }
        Command::RemoveDependency {
            task_id,
            depends_on,
        } => {
            id(task_id)?;
            id(depends_on)?;
            if task_id == depends_on {
                return Err("a task cannot depend on itself".into());
            }
            mutation(&request, Mutation {
                entity: "DELETE FROM frog_model_dependencies WHERE workspace_id=?1 AND task_id=?5 AND depends_on=?6",
                effect: json!(task_id), secondary: json!(depends_on), extras: Vec::new(),
                precheck: Some(&format!("UPDATE frog_model_operations SET changed=1 WHERE {PENDING} AND {EDGE}")),
                satisfied: &format!("NOT {EDGE}"),
                event: "task.dependency_removed", entity_kind: "task",
            })
        }
        Command::ClaimTask {
            task_id,
            expected_revision,
        } => {
            id(task_id)?;
            mutation(&request, Mutation {
                entity: "UPDATE frog_model_tasks SET status='in_progress',assignee=?4,revision=revision+1,last_operation_id=?2,\
status_observed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?1 AND task_id=?5 AND revision=?6 \
AND status='todo' AND assignee IS NULL AND NOT EXISTS(SELECT 1 FROM frog_model_dependencies d \
LEFT JOIN frog_model_tasks p ON p.workspace_id=d.workspace_id AND p.task_id=d.depends_on \
WHERE d.workspace_id=?1 AND d.task_id=?5 AND (p.status IS NULL OR p.status!='done'))",
                effect: json!(task_id), secondary: json!(expected_revision), extras: Vec::new(),
                precheck: None, satisfied: WROTE, event: "task.claimed", entity_kind: "task",
            })
        }
        Command::TransitionTask {
            task_id,
            expected_revision,
            status,
        } => {
            id(task_id)?;
            let previous = TRANSITIONS
                .iter()
                .find(|(next, _)| next == status)
                .map(|(_, previous)| *previous)
                .ok_or("transition requires review, published or done")?;
            mutation(&request, Mutation {
                entity: "UPDATE frog_model_tasks SET status=?7,revision=revision+1,last_operation_id=?2,\
status_observed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?1 AND task_id=?5 AND revision=?6 \
AND assignee=?4 AND status=?8",
                effect: json!(task_id), secondary: json!(expected_revision),
                extras: vec![json!(status), json!(previous)],
                precheck: None, satisfied: WROTE, event: "task.transitioned", entity_kind: "task",
            })
        }
        Command::EditTask {
            task_id,
            expected_revision,
            title: label,
            priority,
        } => {
            id(task_id)?;
            if let Some(label) = label {
                title(label)?;
            }
            if priority.is_some_and(|p| !PRIORITIES.contains(&p)) {
                return Err("priority must be 0-3".into());
            }
            if label.is_none() && priority.is_none() {
                return Err("edit requires at least one field".into());
            }
            mutation(&request, Mutation {
                entity: "UPDATE frog_model_tasks SET title=COALESCE(?7,title),priority=COALESCE(?8,priority),revision=revision+1,last_operation_id=?2,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?1 AND task_id=?5 AND revision=?6 AND EXISTS(SELECT 1 FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2 AND request_key=?3 AND status='pending')",
                effect: json!(task_id), secondary: json!(expected_revision),
                extras: vec![json!(label),json!(priority)], precheck: None,
                satisfied: WROTE, event: "task.edited", entity_kind: "task",
            })
        }
        Command::CancelTask {
            task_id,
            expected_revision,
        } => {
            id(task_id)?;
            mutation(&request, Mutation {
                entity: "UPDATE frog_model_tasks SET status='cancelled',revision=revision+1,last_operation_id=?2,\
status_observed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE workspace_id=?1 AND task_id=?5 AND revision=?6 \
AND (assignee IS NULL OR assignee=?4) AND status IN ('todo','in_progress','review','published')",
                effect: json!(task_id), secondary: json!(expected_revision), extras: Vec::new(),
                precheck: None, satisfied: WROTE, event: "task.cancelled", entity_kind: "task",
            })
        }
    }
}

#[cfg(feature = "component")]
mod component;
#[cfg(all(target_arch = "wasm32", not(feature = "component")))]
mod core_wasm;
#[cfg(feature = "sqlite")]
pub mod legacy;
#[cfg(feature = "sqlite")]
pub mod sqlite;
#[cfg(feature = "sqlite")]
pub mod migration;
