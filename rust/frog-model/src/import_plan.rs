//! Portable, migration-only seed plans. Hosts must authenticate the actor and
//! bind the source digest to a verified archive before executing these plans.
use crate::{import_rules, Command, Request, Statement};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};

pub const DATA_FORMAT: &str = "20261020-6";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Context {
    pub workspace_id: String,
    pub actor: String,
    pub source_sha256: String,
}

impl Context {
    pub fn validate(&self) -> Result<(), String> {
        crate::id(&self.workspace_id)?;
        crate::id(&self.actor)?;
        if self.source_sha256.len() != 64 || !self.source_sha256.bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
            return Err("source digest must be lowercase SHA-256".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Repository {
    pub repo_id: String,
    pub title: String,
    pub source_path: String,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Provider {
    pub source: String,
    pub external_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Task {
    pub task_id: String,
    pub repo_id: String,
    pub title: String,
    pub priority: u8,
    pub status: String,
    pub assignee: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub status_observed_at: String,
    pub why: Option<String>,
    pub what_text: Option<String>,
    pub roi_note: Option<String>,
    pub provider: Option<Provider>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Dependency {
    pub task_id: String,
    pub depends_on: String,
    pub created_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(tag = "kind", content = "data", rename_all = "snake_case", deny_unknown_fields)]
pub enum Seed {
    Repository(Repository),
    Task(Task),
    Dependency(Dependency),
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SeedPlan {
    pub schema: String,
    pub data_format_version: crate::data_format::DataFormatVersion,
    pub normalized: Seed,
    pub operation_id: String,
    pub request_key: String,
    pub statements: Vec<Statement>,
}

fn validate_command(context: &Context, command: Command) -> Result<(), String> {
    crate::plan(Request { workspace_id: context.workspace_id.clone(), actor: context.actor.clone(),
        operation_id: "migration-validation".into(), command }).map(|_| ())
}

/// No upsert/replay here: a duplicate identity or receipt must fail atomically.
/// Baseline revision zero is explicitly new, never a claimed legacy revision.
pub fn plan(context: &Context, mut seed: Seed) -> Result<SeedPlan, String> {
    context.validate()?;
    let request = serde_json::to_vec(&(DATA_FORMAT, context, &seed)).map_err(|e| e.to_string())?;
    let request_key = format!("{:x}", Sha256::digest(request));
    let operation_id = format!("migration-{request_key}");
    let mut statements = vec![crate::statement(
        "INSERT INTO frog_model_operations(workspace_id,operation_id,request_key,status,changed) VALUES(?1,?2,?3,'applied',1)",
        vec![json!(context.workspace_id), json!(operation_id), json!(request_key)])];
    match &mut seed {
        Seed::Repository(repo) => {
            validate_command(context, Command::RegisterRepo { repo_id: repo.repo_id.clone(), title: repo.title.clone() })?;
            if repo.source_path.is_empty() || repo.source_path.len() > 4096 { return Err("invalid source repository path".into()); }
            repo.created_at = import_rules::utc_timestamp(&repo.created_at)?;
            repo.updated_at = import_rules::utc_timestamp(&repo.updated_at)?;
            statements.push(crate::statement(
                "INSERT INTO frog_model_repos(workspace_id,repo_id,title,last_operation_id) VALUES(?1,?2,?3,?4)",
                vec![json!(context.workspace_id), json!(repo.repo_id), json!(repo.title), json!(operation_id)]));
            statements.push(crate::statement(
                "INSERT INTO frog_migration_repo_origins VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",
                vec![json!(context.workspace_id), json!(repo.repo_id), json!(context.source_sha256), json!(repo.source_path),
                    json!(repo.created_at), json!(repo.updated_at), json!(DATA_FORMAT), json!(context.actor)]));
        }
        Seed::Task(task) => {
            validate_command(context, Command::CreateTask { task_id: task.task_id.clone(), repo_id: task.repo_id.clone(),
                title: task.title.clone(), priority: task.priority })?;
            import_rules::status(&task.status)?;
            if let Some(assignee) = &task.assignee { crate::id(assignee)?; }
            if task.why.is_some() || task.what_text.is_some() || task.roi_note.is_some() {
                validate_command(context, Command::EditTaskFields { task_id: task.task_id.clone(), expected_revision: 0,
                    fields: crate::TaskEditFields { why: task.why.clone(), what_text: task.what_text.clone(),
                        roi_note: task.roi_note.clone(), ..Default::default() } })?;
            }
            if let Some(provider) = &task.provider {
                validate_command(context, Command::ProviderImport { source: provider.source.clone(), external_id: provider.external_id.clone(),
                    repo_id: task.repo_id.clone(), task_id: task.task_id.clone(), title: task.title.clone(), priority: task.priority,
                    status: task.status.clone(), expected_revision: None, why: task.why.clone(), what_text: task.what_text.clone() })?;
            }
            task.created_at = import_rules::utc_timestamp(&task.created_at)?;
            task.updated_at = import_rules::utc_timestamp(&task.updated_at)?;
            task.status_observed_at = import_rules::utc_timestamp(&task.status_observed_at)?;
            statements.push(crate::statement(
                "INSERT INTO frog_model_tasks(workspace_id,task_id,repo_id,title,priority,status,assignee,revision,last_operation_id,updated_at,status_observed_at) VALUES(?1,?2,?3,?4,?5,?6,?7,0,?8,?9,?10)",
                vec![json!(context.workspace_id), json!(task.task_id), json!(task.repo_id), json!(task.title), json!(task.priority),
                    json!(task.status), json!(task.assignee), json!(operation_id), json!(task.updated_at), json!(task.status_observed_at)]));
            statements.push(crate::statement("INSERT INTO frog_model_task_details VALUES(?1,?2,?3,?4,?5)",
                vec![json!(context.workspace_id), json!(task.task_id), json!(task.why), json!(task.what_text), json!(task.roi_note)]));
            if let Some(provider) = &task.provider {
                statements.push(crate::statement("INSERT INTO frog_model_provider_items VALUES(?1,?2,?3,?4)",
                    vec![json!(context.workspace_id), json!(provider.source), json!(provider.external_id), json!(task.task_id)]));
            }
            statements.push(crate::statement("INSERT INTO frog_migration_task_origins VALUES(?1,?2,?3,?4,0,NULL,?5,?6)",
                vec![json!(context.workspace_id), json!(task.task_id), json!(context.source_sha256), json!(task.created_at),
                    json!(DATA_FORMAT), json!(context.actor)]));
        }
        Seed::Dependency(edge) => {
            validate_command(context, Command::AddDependency { task_id: edge.task_id.clone(), depends_on: edge.depends_on.clone() })?;
            edge.created_at = import_rules::utc_timestamp(&edge.created_at)?;
            statements.push(crate::statement(
                "WITH RECURSIVE reachable(id) AS (SELECT ?3 UNION SELECT d.depends_on FROM frog_model_dependencies d JOIN reachable r ON d.task_id=r.id WHERE d.workspace_id=?1) INSERT INTO frog_model_dependencies(workspace_id,task_id,depends_on) SELECT ?1,?2,?3 WHERE NOT EXISTS(SELECT 1 FROM reachable WHERE id=?2)",
                vec![json!(context.workspace_id), json!(edge.task_id), json!(edge.depends_on)]));
            statements.push(crate::statement("INSERT INTO frog_migration_dependency_origins VALUES(?1,?2,?3,?4,?5,?6,?7)",
                vec![json!(context.workspace_id), json!(edge.task_id), json!(edge.depends_on), json!(context.source_sha256),
                    json!(edge.created_at), json!(DATA_FORMAT), json!(context.actor)]));
        }
    }
    Ok(SeedPlan { schema: "frog.migration-seed-plan.v1".into(), data_format_version: DATA_FORMAT.parse()?,
        normalized: seed, operation_id, request_key, statements })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn context() -> Context {
        Context { workspace_id: "workspace".into(), actor: "importer".into(), source_sha256: "a".repeat(64) }
    }

    fn task() -> Task {
        Task { task_id: "task".into(), repo_id: "repo:one".into(), title: "Retain status".into(), priority: 1,
            status: "review".into(), assignee: Some("original-agent".into()), created_at: "2026-10-20T00:00:00+00:00".into(),
            updated_at: "2026-10-20T01:00:00Z".into(), status_observed_at: "2026-10-20T00:30:00Z".into(),
            why: Some("".into()), what_text: None, roi_note: Some("Preserve metadata".into()),
            provider: Some(Provider { source: "github".into(), external_id: "owner/repo#1".into() }) }
    }

    #[test]
    fn seed_plans_bind_source_workspace_actor_and_original_values_without_workflow_transitions() {
        let first = plan(&context(), Seed::Task(task())).unwrap();
        assert_eq!(first.request_key, plan(&context(), Seed::Task(task())).unwrap().request_key);
        let Seed::Task(normalized) = first.normalized else { panic!() };
        assert_eq!(normalized.status, "review");
        assert_eq!(normalized.assignee.as_deref(), Some("original-agent"));
        assert_eq!(normalized.created_at, "2026-10-20T00:00:00Z");
        assert_eq!(normalized.updated_at, task().updated_at);
        assert_eq!(normalized.why.as_deref(), Some(""));
        assert_eq!(normalized.what_text, None);
        for changed in [Context { workspace_id: "other".into(), ..context() },
            Context { actor: "other".into(), ..context() }, Context { source_sha256: "b".repeat(64), ..context() }] {
            assert_ne!(plan(&changed, Seed::Task(task())).unwrap().request_key, first.request_key);
        }
        let mut changed = task();
        changed.title = "Different original value".into();
        assert_ne!(plan(&context(), Seed::Task(changed)).unwrap().request_key, first.request_key);
    }

    #[test]
    fn unsupported_values_are_refused_instead_of_truncated_reassigned_or_coerced() {
        for mutate in [
            |t: &mut Task| t.status = "idea".into(),
            |t: &mut Task| t.status = "blocked".into(),
            |t: &mut Task| t.priority = 4,
            |t: &mut Task| t.assignee = Some("invalid@actor".into()),
            |t: &mut Task| t.why = Some("x".repeat(4097)),
            |t: &mut Task| t.updated_at = "unknown".into(),
            |t: &mut Task| t.provider.as_mut().unwrap().external_id = "".into(),
        ] {
            let mut value = task();
            mutate(&mut value);
            assert!(plan(&context(), Seed::Task(value)).is_err());
        }
        assert!(plan(&Context { source_sha256: "not-a-digest".into(), ..context() }, Seed::Task(task())).is_err());
        let mut json = serde_json::to_value(task()).unwrap();
        json["revision"] = json!(99);
        assert!(serde_json::from_value::<Task>(json).is_err());
        let mut unassigned = task();
        unassigned.assignee = None;
        let Seed::Task(normalized) = plan(&context(), Seed::Task(unassigned)).unwrap().normalized else { panic!() };
        assert_eq!(normalized.assignee, None);
    }
}
