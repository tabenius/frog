//! Explicit model projection on a disposable destination, after lossless archive
//! verification. Counts/hashes describe supported fields, never whole-cutover parity.
use crate::{import_plan::{self, Context, Dependency, Provider, Repository, Seed, SeedPlan, Task},
    import_rules, migration::{self, Inventory}, migration_dry_run::{self, DryRun},
    migration_simulation::{self, Preservation}};
use rusqlite::{params, types::Value as SqlValue, Connection, Row};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, BTreeSet}, path::Path};

const SCHEMA: &str = include_str!("../sql/0004_migration_projection.sql");

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Finding {
    pub code: String,
    pub table: String,
    pub count: u64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ModelProjection {
    pub repositories: u64,
    pub tasks: u64,
    pub dependencies: u64,
    pub providers: u64,
    pub skipped_repositories: u64,
    pub skipped_tasks: u64,
    pub skipped_dependencies: u64,
    pub source_model_sha256: String,
    pub destination_model_sha256: String,
    pub initial_revision: u32,
    pub fields_verified: bool,
    pub findings: Vec<Finding>,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Projection {
    pub schema: String,
    pub data_format_version: crate::data_format::DataFormatVersion,
    pub workspace_id: String,
    pub assessment: DryRun,
    pub preservation: Preservation,
    pub model: ModelProjection,
    pub destination_simulated: bool,
    pub operational_model_populated: bool,
    pub ready_to_apply: bool,
}

struct Expected {
    seed: Seed,
    operation_id: String,
    request_key: String,
}

type Findings = BTreeMap<(String, String), u64>;
fn finding(findings: &mut Findings, code: &str, table: &str, count: u64) {
    *findings.entry((code.into(), table.into())).or_default() += count;
}

pub fn project(source_path: &Path, workspace: &str, snapshot_path: Option<&Path>) -> Result<Projection, String> {
    crate::id(workspace)?;
    let source = migration::capture(source_path)?;
    let assessment = migration_dry_run::assess_snapshot(&source)?;
    let context = Context { workspace_id: workspace.into(), actor: "migration-projection".into(),
        source_sha256: assessment.inventory.snapshot_sha256.clone() };
    let mut destination = migration_simulation::destination()?;
    migration_simulation::preserve(&source, &assessment.inventory, &mut destination)?;
    let model = project_into(&source, &assessment.inventory, &context, &mut destination)?;
    let preservation = migration_simulation::verify(&destination, &assessment.inventory)?;
    if let Some(path) = snapshot_path { migration::write_snapshot(&source, path)?; }
    let populated = model.repositories > 0 || model.tasks > 0;
    Ok(Projection { schema: "frog.migration-projection.v1".into(), data_format_version: import_plan::DATA_FORMAT.parse()?,
        workspace_id: workspace.into(), assessment, preservation, model, destination_simulated: true,
        operational_model_populated: populated, ready_to_apply: false })
}

fn has(inventory: &Inventory, table: &str, column: &str) -> bool {
    inventory.tables.iter().any(|t| t.name == table && t.columns.iter().any(|c| c.name == column))
}

fn required(inventory: &Inventory, table: &str, columns: &[&str]) -> bool {
    columns.iter().all(|column| has(inventory, table, column))
}

fn source_rows(inventory: &Inventory, table: &str) -> u64 {
    inventory.tables.iter().find(|t| t.name == table).map_or(0, |t| t.rows)
}

fn project_into(source: &Connection, inventory: &Inventory, context: &Context, destination: &mut Connection) -> Result<ModelProjection, String> {
    context.validate()?;
    if context.source_sha256 != inventory.snapshot_sha256 {
        return Err("projection source context does not match verified inventory".into());
    }
    let snapshot = source.serialize("main").map_err(error)?;
    if format!("{:x}", Sha256::digest(&*snapshot)) != context.source_sha256 {
        return Err("projection source changed after inventory".into());
    }
    // Re-anchor to the archive before any model writes. Only a fresh operational
    // model may be seeded; do not overwrite/replay into an active workspace.
    migration_simulation::verify(destination, inventory)?;
    for table in ["frog_model_workspaces", "frog_model_repos", "frog_model_tasks", "frog_model_operations", "frog_model_dependencies", "frog_model_events"] {
        let count: i64 = destination.query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0)).map_err(error)?;
        if count != 0 { return Err("projection requires an empty operational destination".into()); }
    }
    let transaction = destination.transaction().map_err(error)?;
    transaction.execute_batch(SCHEMA).map_err(error)?;
    transaction.execute("INSERT INTO frog_model_workspaces VALUES(?1,'Disposable legacy projection')", [&context.workspace_id]).map_err(error)?;
    let mut expected = Vec::new();
    let mut findings = Findings::new();
    let mut repos = BTreeMap::new();
    if required(inventory, "repos", &["repo_path", "repo_key", "name", "created_at", "updated_at"]) {
        let mut statement = source.prepare("SELECT repo_path,name,repo_key,created_at,updated_at,
            count(*) OVER(PARTITION BY repo_key),count(*) OVER(PARTITION BY repo_path) FROM repos ORDER BY repo_key,repo_path").map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            if row.get::<_, i64>(5).map_err(error)? > 1 || row.get::<_, i64>(6).map_err(error)? > 1 {
                finding(&mut findings, "repository_identity_ambiguous", "repos", 1);
                continue;
            }
            let repo = match read_repository(row) {
                Ok(repo) => repo,
                Err(_) => { finding(&mut findings, "repository_required_value_invalid", "repos", 1); continue; }
            };
            let plan = match import_plan::plan(context, Seed::Repository(repo)) {
                Ok(plan) => plan,
                Err(_) => { finding(&mut findings, "repository_rejected_by_import_plan", "repos", 1); continue; }
            };
            if let Seed::Repository(repo) = &plan.normalized { repos.insert(repo.source_path.clone(), repo.repo_id.clone()); }
            execute(&transaction, &plan)?;
            expected.push(expectation(plan));
        }
    } else { finding(&mut findings, "repository_schema_incomplete", "repos", source_rows(inventory, "repos")); }
    let mut task_ids = BTreeSet::new();
    if has(inventory, "tasks", "revision") {
        finding(&mut findings, "source_revision_requires_explicit_mapping", "tasks", source_rows(inventory, "tasks"));
    } else if required(inventory, "tasks", &["slug", "repo_path", "title", "priority", "workflow_status", "assigned_agent", "created_at", "updated_at", "status_confidence_at"]) {
        let optional = ["why", "what_text", "roi_note", "source", "external_id"].map(|name|
            if has(inventory, "tasks", name) { name } else { "NULL" });
        let sql = format!("SELECT slug,repo_path,title,priority,workflow_status,assigned_agent,created_at,updated_at,status_confidence_at,{},{},{},{},{},count(*) OVER(PARTITION BY slug),count(*) OVER(PARTITION BY {},{}) FROM tasks ORDER BY slug",
            optional[0], optional[1], optional[2], optional[3], optional[4], optional[3], optional[4]);
        let mut statement = source.prepare(&sql).map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            if row.get::<_, i64>(14).map_err(error)? > 1 {
                finding(&mut findings, "task_identity_ambiguous", "tasks", 1); continue;
            }
            let task = match read_task(row, &repos) {
                Ok(task) => task,
                Err(code) => { finding(&mut findings, code, "tasks", 1); continue; }
            };
            if task.provider.is_some() && row.get::<_, i64>(15).map_err(error)? > 1 {
                finding(&mut findings, "provider_identity_ambiguous", "tasks", 1); continue;
            }
            let plan = match import_plan::plan(context, Seed::Task(task)) {
                Ok(plan) => plan,
                Err(_) => { finding(&mut findings, "task_rejected_by_import_plan", "tasks", 1); continue; }
            };
            if let Seed::Task(task) = &plan.normalized { task_ids.insert(task.task_id.clone()); }
            execute(&transaction, &plan)?;
            expected.push(expectation(plan));
        }
    } else { finding(&mut findings, "task_schema_incomplete", "tasks", source_rows(inventory, "tasks")); }
    if required(inventory, "task_dependencies", &["task_slug", "depends_on_slug", "relation", "created_at"]) {
        let mut statement = source.prepare("SELECT task_slug,depends_on_slug,relation,created_at,
            count(*) OVER(PARTITION BY task_slug,depends_on_slug,relation) FROM task_dependencies ORDER BY task_slug,depends_on_slug,relation").map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            let edge = match read_dependency(row, &task_ids) {
                Ok(edge) => edge,
                Err(code) => { finding(&mut findings, code, "task_dependencies", 1); continue; }
            };
            let plan = match import_plan::plan(context, Seed::Dependency(edge)) {
                Ok(plan) => plan,
                Err(_) => { finding(&mut findings, "dependency_rejected_by_import_plan", "task_dependencies", 1); continue; }
            };
            // A cycle or foreign-key failure aborts the complete model projection.
            execute(&transaction, &plan)?;
            expected.push(expectation(plan));
        }
    } else { finding(&mut findings, "dependency_schema_incomplete", "task_dependencies", source_rows(inventory, "task_dependencies")); }
    let (source_model_sha256, destination_model_sha256) = reconcile(&transaction, context, &expected)?;
    transaction.commit().map_err(error)?;
    let (_, after_commit) = reconcile(destination, context, &expected)?;
    if after_commit != destination_model_sha256 { return Err("model projection changed after commit".into()); }
    let repositories = repos.len() as u64;
    let tasks = task_ids.len() as u64;
    let dependencies = expected.iter().filter(|e| matches!(e.seed, Seed::Dependency(_))).count() as u64;
    let providers = expected.iter().filter(|e| matches!(&e.seed, Seed::Task(task) if task.provider.is_some())).count() as u64;
    Ok(ModelProjection { repositories, tasks, dependencies, providers,
        skipped_repositories: source_rows(inventory, "repos").saturating_sub(repositories),
        skipped_tasks: source_rows(inventory, "tasks").saturating_sub(tasks),
        skipped_dependencies: source_rows(inventory, "task_dependencies").saturating_sub(dependencies),
        source_model_sha256, destination_model_sha256, initial_revision: 0, fields_verified: true,
        findings: findings.into_iter().map(|((code, table), count)| Finding { code, table, count }).collect() })
}

fn read_repository(row: &Row<'_>) -> rusqlite::Result<Repository> {
    Ok(Repository { source_path: row.get(0)?, title: row.get(1)?, repo_id: row.get(2)?, created_at: row.get(3)?, updated_at: row.get(4)? })
}

fn read_task(row: &Row<'_>, repos: &BTreeMap<String, String>) -> Result<Task, &'static str> {
    let value = |index| row.get::<_, String>(index).map_err(|_| "task_required_value_invalid");
    let nullable = |index| row.get::<_, Option<String>>(index).map_err(|_| "task_optional_value_invalid");
    let repo_id = repos.get(&value(1)?).ok_or("task_repository_not_projected")?.clone();
    let provider = match (nullable(12)?, nullable(13)?) {
        (None, None) => None,
        (Some(source), Some(external_id)) if !source.is_empty() && !external_id.is_empty() => Some(Provider { source, external_id }),
        _ => return Err("provider_identity_incomplete"),
    };
    Ok(Task { task_id: value(0)?, repo_id, title: value(2)?, priority: import_rules::priority(&value(3)?)?,
        status: value(4)?, assignee: nullable(5)?, created_at: value(6)?, updated_at: value(7)?, status_observed_at: value(8)?,
        why: nullable(9)?, what_text: nullable(10)?, roi_note: nullable(11)?, provider })
}

fn read_dependency(row: &Row<'_>, tasks: &BTreeSet<String>) -> Result<Dependency, &'static str> {
    let value = |index| row.get::<_, String>(index).map_err(|_| "dependency_required_value_invalid");
    if value(2)? != "depends_on" { return Err("dependency_relation_not_supported"); }
    if row.get::<_, i64>(4).map_err(|_| "dependency_required_value_invalid")? > 1 { return Err("dependency_identity_ambiguous"); }
    let edge = Dependency { task_id: value(0)?, depends_on: value(1)?, created_at: value(3)? };
    if !tasks.contains(&edge.task_id) || !tasks.contains(&edge.depends_on) { return Err("dependency_endpoint_not_projected"); }
    Ok(edge)
}

fn execute(db: &Connection, plan: &SeedPlan) -> Result<(), String> {
    if plan.schema != "frog.migration-seed-plan.v1" || plan.data_format_version.to_string() != import_plan::DATA_FORMAT {
        return Err("unsupported migration seed plan".into());
    }
    for statement in &plan.statements {
        let values = statement.params.iter().map(|v| match v {
            serde_json::Value::Null => Ok(SqlValue::Null),
            serde_json::Value::String(v) => Ok(SqlValue::Text(v.clone())),
            serde_json::Value::Number(v) => v.as_i64().map(SqlValue::Integer).ok_or("invalid seed number"),
            _ => Err("invalid seed parameter"),
        }).collect::<Result<Vec<_>, _>>()?;
        if db.execute(&statement.sql, rusqlite::params_from_iter(values)).map_err(error)? != 1 {
            return Err("migration seed did not write exactly one row (cycle or conflict)".into());
        }
    }
    Ok(())
}

fn expectation(plan: SeedPlan) -> Expected {
    Expected { seed: plan.normalized, operation_id: plan.operation_id, request_key: plan.request_key }
}

fn reconcile(db: &Connection, context: &Context, expected: &[Expected]) -> Result<(String, String), String> {
    let workspace_title: String = db.query_row("SELECT title FROM frog_model_workspaces WHERE workspace_id=?1",
        [&context.workspace_id], |r| r.get(0)).map_err(error)?;
    if workspace_title != "Disposable legacy projection" { return Err("projection workspace context mismatch".into()); }
    let mut source_values = Vec::new();
    let mut destination_values = Vec::new();
    let mut repo_count = 0;
    let mut task_count = 0;
    let mut edge_count = 0;
    let mut provider_count = 0;
    for item in expected {
        let actual = match &item.seed {
            Seed::Repository(repo) => {
                repo_count += 1;
                let actual = db.query_row("SELECT r.repo_id,r.title,o.source_path,o.source_created_at,o.source_updated_at FROM frog_model_repos r JOIN frog_migration_repo_origins o USING(workspace_id,repo_id) WHERE r.workspace_id=?1 AND r.repo_id=?2 AND o.source_id=?3 AND o.mapping_version=?4 AND o.actor=?5 AND r.last_operation_id=?6",
                    params![context.workspace_id, repo.repo_id, context.source_sha256, import_plan::DATA_FORMAT, context.actor, item.operation_id],
                    |r| Ok(Repository { repo_id:r.get(0)?,title:r.get(1)?,source_path:r.get(2)?,created_at:r.get(3)?,updated_at:r.get(4)? })).map_err(error)?;
                Seed::Repository(actual)
            }
            Seed::Task(task) => {
                task_count += 1;
                provider_count += u64::from(task.provider.is_some());
                let actual = db.query_row("SELECT t.task_id,t.repo_id,t.title,t.priority,t.status,t.assignee,o.source_created_at,t.updated_at,t.status_observed_at,d.why,d.what_text,d.roi_note,p.source,p.external_id FROM frog_model_tasks t JOIN frog_migration_task_origins o USING(workspace_id,task_id) JOIN frog_model_task_details d USING(workspace_id,task_id) LEFT JOIN frog_model_provider_items p USING(workspace_id,task_id) WHERE t.workspace_id=?1 AND t.task_id=?2 AND o.source_id=?3 AND o.mapping_version=?4 AND o.actor=?5 AND t.last_operation_id=?6 AND t.revision=0 AND o.baseline_revision=0 AND o.source_revision IS NULL",
                    params![context.workspace_id, task.task_id, context.source_sha256, import_plan::DATA_FORMAT, context.actor, item.operation_id], |r| {
                        let source: Option<String> = r.get(12)?;
                        let external_id: Option<String> = r.get(13)?;
                        Ok(Task { task_id:r.get(0)?,repo_id:r.get(1)?,title:r.get(2)?,priority:r.get(3)?,status:r.get(4)?,assignee:r.get(5)?,
                            created_at:r.get(6)?,updated_at:r.get(7)?,status_observed_at:r.get(8)?,why:r.get(9)?,what_text:r.get(10)?,roi_note:r.get(11)?,
                            provider:source.zip(external_id).map(|(source,external_id)| Provider {source,external_id}) })
                    }).map_err(error)?;
                Seed::Task(actual)
            }
            Seed::Dependency(edge) => {
                edge_count += 1;
                let actual = db.query_row("SELECT d.task_id,d.depends_on,o.source_created_at FROM frog_model_dependencies d JOIN frog_migration_dependency_origins o USING(workspace_id,task_id,depends_on) WHERE d.workspace_id=?1 AND d.task_id=?2 AND d.depends_on=?3 AND o.source_id=?4 AND o.mapping_version=?5 AND o.actor=?6",
                    params![context.workspace_id, edge.task_id, edge.depends_on, context.source_sha256, import_plan::DATA_FORMAT, context.actor],
                    |r| Ok(Dependency { task_id:r.get(0)?,depends_on:r.get(1)?,created_at:r.get(2)? })).map_err(error)?;
                Seed::Dependency(actual)
            }
        };
        if actual != item.seed { return Err("source-to-model field reconciliation failed".into()); }
        let receipt: (String,String,i64) = db.query_row("SELECT request_key,status,changed FROM frog_model_operations WHERE workspace_id=?1 AND operation_id=?2",
            params![context.workspace_id,item.operation_id], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?))).map_err(error)?;
        if receipt != (item.request_key.clone(), "applied".into(), 1) { return Err("migration receipt mismatch".into()); }
        source_values.push(seed_digest(&item.seed)?);
        destination_values.push(seed_digest(&actual)?);
    }
    for (table, count) in [("frog_model_repos",repo_count),("frog_model_tasks",task_count),("frog_model_task_details",task_count),
        ("frog_model_dependencies",edge_count),("frog_model_provider_items",provider_count),("frog_model_operations",expected.len() as u64),
        ("frog_migration_repo_origins",repo_count),("frog_migration_task_origins",task_count),("frog_migration_dependency_origins",edge_count),
        ("frog_model_events",0),("frog_model_locks",0),("frog_model_verifications",0),("frog_model_build_runs",0),("frog_model_workspaces",1)] {
        let actual: i64 = db.query_row(&format!("SELECT count(*) FROM {table}"), [], |r| r.get(0)).map_err(error)?;
        if actual as u64 != count { return Err("unexpected operational projection records".into()); }
    }
    let foreign_key_errors = db.prepare("PRAGMA foreign_key_check").map_err(error)?.query_map([], |_| Ok(())).map_err(error)?.count();
    let integrity: String = db.query_row("PRAGMA integrity_check", [], |r| r.get(0)).map_err(error)?;
    if foreign_key_errors != 0 || integrity != "ok" { return Err("projected destination integrity failure".into()); }
    Ok((fingerprint(source_values), fingerprint(destination_values)))
}

fn seed_digest(seed: &Seed) -> Result<[u8;32],String> {
    let bytes = serde_json::to_vec(seed).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    hash.update(b"frog.migration.projected-seed.v1\0");
    hash.update((bytes.len() as u64).to_be_bytes());
    hash.update(bytes);
    Ok(hash.finalize().into())
}

fn fingerprint(mut values: Vec<[u8;32]>) -> String {
    values.sort_unstable();
    let mut hash = Sha256::new();
    hash.update(b"frog.migration.projected-fields.v1\0");
    hash.update((values.len() as u64).to_be_bytes());
    for value in values { hash.update((value.len() as u64).to_be_bytes()); hash.update(value); }
    format!("{:x}", hash.finalize())
}

fn error(error: rusqlite::Error) -> String { format!("model projection failed: {error}") }

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Command, Request};

    fn source() -> Connection {
        let db = Connection::open_in_memory().unwrap();
        db.execute_batch("PRAGMA foreign_keys=OFF;").unwrap();
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../src/ragbaz_frog/migrations");
        let mut paths = std::fs::read_dir(root).unwrap().map(|entry| entry.unwrap().path())
            .filter(|p| p.extension().is_some_and(|e| e == "sql")).collect::<Vec<_>>();
        paths.sort();
        for path in paths { db.execute_batch(&std::fs::read_to_string(path).unwrap()).unwrap(); }
        db.execute_batch("INSERT INTO repos(repo_path,name,repo_key,created_at,updated_at)
            VALUES('/private/frog','Frog','repo:frog','2026-10-20T00:00:00+00:00','2026-10-20T01:00:00Z');
            INSERT INTO tasks(slug,repo_path,title,priority,workflow_status,assigned_agent,created_at,updated_at,status_confidence_at,why,roi_note,source,external_id)
            VALUES('ready','/private/frog','Original title','p1','todo',NULL,'2026-10-20T00:00:00+00:00','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z','','ROI note','github','owner/repo#1'),
            ('finished','/private/frog','Finished task','p2','done','legacy-agent','2026-10-20T00:00:00Z','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z',NULL,NULL,NULL,NULL),
            ('active','/private/frog','Active task','p0','in_progress','legacy-agent','2026-10-20T00:00:00Z','2026-10-20T01:00:00Z','2026-10-20T00:30:00Z',NULL,NULL,NULL,NULL);
            INSERT INTO task_dependencies VALUES('ready','finished','depends_on','2026-10-20T00:00:00Z'),
                ('ready','active','depends_on','2026-10-20T00:00:00Z');").unwrap();
        db
    }

    fn setup(source: &Connection) -> (Inventory, Context, Connection) {
        let inventory = migration::inventory_snapshot(source).unwrap();
        let context = Context { workspace_id: "chosen-workspace".into(), actor: "migration-projection".into(),
            source_sha256: inventory.snapshot_sha256.clone() };
        let mut destination = migration_simulation::destination().unwrap();
        migration_simulation::preserve(source, &inventory, &mut destination).unwrap();
        (inventory, context, destination)
    }

    fn request(context: &Context, actor: &str, command: Command) -> Request {
        Request { workspace_id: context.workspace_id.clone(), actor: actor.into(), operation_id: "read-or-act".into(), command }
    }

    fn expected_from_fixture(source: &Connection, context: &Context) -> Vec<Expected> {
        let mut expected = Vec::new();
        let mut statement = source.prepare("SELECT repo_path,name,repo_key,created_at,updated_at FROM repos").unwrap();
        let mut rows = statement.query([]).unwrap();
        while let Some(row) = rows.next().unwrap() {
            expected.push(expectation(import_plan::plan(context,Seed::Repository(read_repository(row).unwrap())).unwrap()));
        }
        let repos = BTreeMap::from([("/private/frog".into(),"repo:frog".into())]);
        let mut statement = source.prepare("SELECT slug,repo_path,title,priority,workflow_status,assigned_agent,created_at,updated_at,status_confidence_at,why,what_text,roi_note,source,external_id FROM tasks").unwrap();
        let mut rows = statement.query([]).unwrap();
        let mut ids = BTreeSet::new();
        while let Some(row) = rows.next().unwrap() {
            let task = read_task(row,&repos).unwrap();
            ids.insert(task.task_id.clone());
            expected.push(expectation(import_plan::plan(context,Seed::Task(task)).unwrap()));
        }
        let mut statement = source.prepare("SELECT task_slug,depends_on_slug,relation,created_at,1 FROM task_dependencies").unwrap();
        let mut rows = statement.query([]).unwrap();
        while let Some(row) = rows.next().unwrap() {
            expected.push(expectation(import_plan::plan(context,Seed::Dependency(read_dependency(row,&ids).unwrap())).unwrap()));
        }
        expected
    }

    #[test]
    fn projected_data_works_through_existing_model_reads_and_revisioned_rules() {
        let source = source();
        let before = source.serialize("main").unwrap().to_vec();
        let (inventory, context, mut destination) = setup(&source);
        let result = project_into(&source, &inventory, &context, &mut destination).unwrap();
        assert_eq!((result.repositories,result.tasks,result.dependencies,result.providers), (1,3,2,1));
        assert_eq!((result.skipped_repositories,result.skipped_tasks,result.skipped_dependencies), (0,0,0));
        assert_eq!(result.source_model_sha256, result.destination_model_sha256);
        assert!(result.fields_verified);
        let board = crate::sqlite::execute(&mut destination, request(&context, "reader", Command::Board { repo_id:None,limit:100,after:None })).unwrap();
        let tasks = board["items"].as_array().unwrap();
        let ready = tasks.iter().find(|task| task["id"] == "ready").unwrap();
        assert_eq!(ready["status"], "todo");
        assert_eq!(ready["assignee"], serde_json::Value::Null);
        assert_eq!(ready["revision"], 0);
        assert_eq!(ready["updated_at"], "2026-10-20T01:00:00Z");
        assert_eq!(ready["status_observed_at"], "2026-10-20T00:30:00Z");
        assert_eq!(ready["lane"], "blocked");
        assert_eq!(ready["unmet_dependencies"], serde_json::json!(["active"]));
        assert_eq!(tasks.iter().find(|task| task["id"] == "active").unwrap()["assignee"], "legacy-agent");
        let details = crate::sqlite::execute(&mut destination, request(&context,"reader",Command::TaskDetails {task_id:"ready".into()})).unwrap();
        assert_eq!(details["items"][0]["why"], "");
        assert_eq!(details["items"][0]["what_text"], serde_json::Value::Null);
        assert_eq!(details["items"][0]["roi_note"], "ROI note");
        let provider = crate::sqlite::execute(&mut destination, request(&context,"reader",Command::ProviderOutbox {source:"github".into(),limit:100,after:None})).unwrap();
        assert_eq!(provider["items"][0]["external_id"], "owner/repo#1");
        assert_eq!(provider["items"][0]["status"], "todo");
        assert_eq!(destination.query_row("SELECT source_created_at FROM frog_migration_task_origins WHERE task_id='ready'",[],|r|r.get::<_,String>(0)).unwrap(), "2026-10-20T00:00:00Z");
        assert_eq!(destination.query_row("SELECT count(*) FROM frog_model_events",[],|r|r.get::<_,i64>(0)).unwrap(),0);
        assert_eq!(destination.query_row("SELECT count(*) FROM frog_model_locks",[],|r|r.get::<_,i64>(0)).unwrap(),0);
        let mut outside = request(&context,"reader",Command::Board {repo_id:None,limit:100,after:None});
        outside.workspace_id = "outsider".into();
        assert_eq!(crate::sqlite::execute(&mut destination,outside).unwrap()["items"],serde_json::json!([]));
        let transition = crate::sqlite::execute(&mut destination,request(&context,"legacy-agent",Command::TransitionTask {
            task_id:"active".into(),expected_revision:0,status:"review".into()})).unwrap();
        assert_eq!(transition["items"][0]["status"],"applied");
        assert_eq!(source.serialize("main").unwrap().to_vec(),before);
        assert!(migration_simulation::verify(&destination,&inventory).unwrap().verified);
    }

    #[test]
    fn unsupported_status_provider_or_metadata_values_are_counted_and_remain_archived() {
        let source = source();
        source.execute_batch("UPDATE tasks SET workflow_status='idea' WHERE slug='active';
            UPDATE tasks SET source='github',external_id=NULL WHERE slug='ready';").unwrap();
        let (inventory,context,mut destination) = setup(&source);
        let result = project_into(&source,&inventory,&context,&mut destination).unwrap();
        assert_eq!(result.tasks,1);
        assert_eq!(result.skipped_tasks,2);
        assert_eq!(result.skipped_dependencies,2);
        assert!(result.findings.iter().any(|f|f.code=="provider_identity_incomplete"));
        assert!(migration_simulation::verify(&destination,&inventory).unwrap().verified);
        assert_eq!(inventory.tables.iter().find(|t|t.name=="tasks").unwrap().rows,3);
    }

    #[test]
    fn ambiguous_repository_and_provider_identities_are_not_arbitrarily_selected() {
        let source = source();
        source.execute_batch("INSERT INTO repos(repo_path,name,repo_key,created_at,updated_at)
            VALUES('/other','Other','repo:frog','2026-10-20T00:00:00Z','2026-10-20T00:00:00Z');").unwrap();
        let (inventory,context,mut destination) = setup(&source);
        let result = project_into(&source,&inventory,&context,&mut destination).unwrap();
        assert_eq!((result.repositories,result.tasks,result.skipped_repositories),(0,0,2));
        let source = self::source();
        source.execute_batch("DROP INDEX idx_tasks_source_extid; UPDATE tasks SET source='github',external_id='owner/repo#1' WHERE slug='finished';").unwrap();
        let (inventory,context,mut destination) = setup(&source);
        let result = project_into(&source,&inventory,&context,&mut destination).unwrap();
        assert_eq!(result.tasks,1);
        assert_eq!(result.skipped_tasks,2);
        assert!(result.findings.iter().any(|f|f.code=="provider_identity_ambiguous" && f.count==2));
    }

    #[test]
    fn cycle_failure_rolls_back_all_model_seeds_and_keeps_verified_preservation() {
        let source = source();
        source.execute_batch("INSERT INTO task_dependencies VALUES('active','ready','depends_on','2026-10-20T00:00:00Z');").unwrap();
        let (inventory,context,mut destination) = setup(&source);
        assert!(project_into(&source,&inventory,&context,&mut destination).is_err());
        for table in ["frog_model_workspaces","frog_model_repos","frog_model_tasks","frog_model_operations","frog_model_dependencies"] {
            assert_eq!(destination.query_row(&format!("SELECT count(*) FROM {table}"),[],|r|r.get::<_,i64>(0)).unwrap(),0);
        }
        assert!(destination.prepare("SELECT * FROM frog_migration_task_origins").is_err());
        assert!(migration_simulation::verify(&destination,&inventory).unwrap().verified);
    }

    #[test]
    fn revisioned_source_and_existing_destination_are_never_silently_overwritten() {
        let source = source();
        source.execute_batch("ALTER TABLE tasks ADD COLUMN revision INTEGER; UPDATE tasks SET revision=7;").unwrap();
        let (inventory,context,mut destination) = setup(&source);
        let result = project_into(&source,&inventory,&context,&mut destination).unwrap();
        assert_eq!(result.tasks,0);
        assert_eq!(result.skipped_tasks,3);
        assert!(result.findings.iter().any(|f|f.code=="source_revision_requires_explicit_mapping"));
        assert!(project_into(&source,&inventory,&context,&mut destination).is_err());
        assert_eq!(destination.query_row("SELECT count(*) FROM frog_model_repos",[],|r|r.get::<_,i64>(0)).unwrap(),1);
        let source = self::source();
        let (inventory,context,mut destination) = setup(&source);
        source.execute_batch("UPDATE tasks SET title='Changed after archive' WHERE slug='ready'").unwrap();
        assert!(project_into(&source,&inventory,&context,&mut destination).is_err());
        assert_eq!(destination.query_row("SELECT count(*) FROM frog_model_tasks",[],|r|r.get::<_,i64>(0)).unwrap(),0);
    }

    #[test]
    fn source_to_model_verification_detects_field_receipt_and_provenance_tampering() {
        for sql in ["UPDATE frog_model_tasks SET title='tampered' WHERE task_id='ready'",
            "UPDATE frog_model_tasks SET status_observed_at='2026-10-20T02:00:00Z' WHERE task_id='ready'",
            "UPDATE frog_model_tasks SET assignee='importer' WHERE task_id='ready'",
            "UPDATE frog_migration_task_origins SET actor='other' WHERE task_id='ready'",
            "UPDATE frog_model_operations SET request_key='changed'",
            "DELETE FROM frog_model_task_details WHERE task_id='ready'",
            "UPDATE frog_model_provider_items SET external_id='other' WHERE task_id='ready'",
            "DELETE FROM frog_model_dependencies WHERE task_id='ready' AND depends_on='active'"] {
            let source = source();
            let (inventory,context,mut destination) = setup(&source);
            project_into(&source,&inventory,&context,&mut destination).unwrap();
            let expected = expected_from_fixture(&source,&context);
            assert!(reconcile(&destination,&context,&expected).is_ok(),"unmodified fixture must reconcile");
            // Deliberately bypass FK enforcement only to test the verifier's
            // independent detection of broken provenance/relationship records.
            destination.pragma_update(None,"foreign_keys","OFF").unwrap();
            destination.execute_batch(sql).unwrap();
            assert!(reconcile(&destination,&context,&expected).is_err(),"{sql}");
        }
    }
}
