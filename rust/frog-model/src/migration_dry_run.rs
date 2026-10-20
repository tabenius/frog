//! Native snapshot extraction and assessment using the portable mapping rules.
use crate::{import_rules::{self, FieldMapping}, migration::{self, Inventory}, Command, Request};
use rusqlite::{types::ValueRef, Connection, Row};
use serde::{Deserialize, Serialize};
use std::{collections::{BTreeMap, BTreeSet, VecDeque}, path::Path};

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Finding {
    pub code: String,
    pub table: String,
    pub column: Option<String>,
    pub count: u64,
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DryRun {
    pub schema: String,
    pub data_format_version: crate::data_format::DataFormatVersion,
    pub inventory: Inventory,
    pub fields: Vec<FieldMapping>,
    pub findings: Vec<Finding>,
    pub candidate_repositories: u64,
    pub candidate_tasks: u64,
    pub candidate_dependency_edges: u64,
    pub normalized_utc_timestamps: u64,
    pub destination_simulated: bool,
    pub ready_to_apply: bool,
}

type Findings = BTreeMap<(String, String, Option<String>), u64>;
fn finding(findings: &mut Findings, code: &str, table: &str, column: Option<&str>, count: u64) {
    *findings.entry((code.into(), table.into(), column.map(str::to_string))).or_default() += count;
}

/// Inventory and assessments use the same immutable backup, never two live reads.
pub fn dry_run(source: &Path, snapshot_path: Option<&Path>) -> Result<DryRun, String> {
    let db = migration::capture(source)?;
    let inventory = migration::inventory_snapshot(&db)?;
    let mut findings = Findings::new();
    let mut fields = Vec::new();
    for table in &inventory.tables {
        for column in &table.columns {
            let field = import_rules::field_mapping(&table.name, &column.name);
            if field.destination.is_none() {
                finding(&mut findings, "field_requires_schema_or_preservation", &table.name, Some(&column.name), table.rows);
            } else if let Some(classes) = table.storage_classes.get(&column.name) {
                for (class, count) in classes {
                    if class != "null" && class != "text" {
                        finding(&mut findings, "mapped_field_storage_class_mismatch", &table.name, Some(&column.name), *count);
                    }
                }
            }
            fields.push(field);
        }
    }
    let mut candidate_repositories = 0;
    let mut candidate_tasks = 0;
    let mut normalized_utc_timestamps = 0;
    let mut repo_ids = BTreeSet::new();
    let mut repos = BTreeMap::new();
    if required(&inventory, "repos", &["repo_path", "name", "repo_key"], &mut findings) {
        let mut statement = db.prepare("SELECT repo_path,name,repo_key FROM repos").map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            let path = text(row, 0, "repos", "repo_path", &mut findings);
            let name = text(row, 1, "repos", "name", &mut findings);
            let key = text(row, 2, "repos", "repo_key", &mut findings);
            let key = match import_rules::repo_identity(key.as_deref()) {
                Ok(value) => value.to_string(),
                Err(code) => { finding(&mut findings, code, "repos", Some("repo_key"), 1); continue; }
            };
            if !repo_ids.insert(key.clone()) {
                finding(&mut findings, "repository_identity_collision", "repos", Some("repo_key"), 1);
            }
            if let (Some(path), Some(name)) = (path, name) {
                if validates(Command::RegisterRepo { repo_id: key.clone(), title: name }) {
                    candidate_repositories += 1;
                    repos.insert(path, key);
                } else {
                    finding(&mut findings, "repository_rejected_by_model", "repos", None, 1);
                }
            } else {
                finding(&mut findings, "repository_path_or_title_missing", "repos", None, 1);
            }
        }
    }
    let mut task_ids = BTreeSet::new();
    if required(&inventory, "tasks", &["slug", "repo_path", "title", "priority", "workflow_status"], &mut findings) {
        let mut statement = db.prepare("SELECT slug,repo_path,title,priority,workflow_status FROM tasks").map_err(error)?;
        let mut rows = statement.query([]).map_err(error)?;
        while let Some(row) = rows.next().map_err(error)? {
            let slug = text(row, 0, "tasks", "slug", &mut findings);
            let path = text(row, 1, "tasks", "repo_path", &mut findings);
            let title = text(row, 2, "tasks", "title", &mut findings);
            let priority = text(row, 3, "tasks", "priority", &mut findings);
            let status = text(row, 4, "tasks", "workflow_status", &mut findings);
            if let Some(slug) = &slug {
                if !task_ids.insert(slug.clone()) { finding(&mut findings, "duplicate_task_identity", "tasks", Some("slug"), 1); }
            }
            let priority = priority.as_deref().and_then(|value| import_rules::priority(value).ok());
            if priority.is_none() { finding(&mut findings, "unsupported_priority", "tasks", Some("priority"), 1); }
            let status_valid = match status.as_deref().map(import_rules::status) {
                Some(Ok(_)) => true,
                value => {
                    finding(&mut findings, value.and_then(Result::err).unwrap_or("unsupported_status"), "tasks", Some("workflow_status"), 1);
                    false
                }
            };
            let repo_id = path.as_ref().and_then(|path| repos.get(path)).cloned();
            if repo_id.is_none() { finding(&mut findings, "task_repository_unresolved", "tasks", Some("repo_path"), 1); }
            if let (Some(task_id), Some(repo_id), Some(title), Some(priority)) = (slug, repo_id, title, priority) {
                if validates(Command::CreateTask { repo_id, task_id, title, priority }) {
                    if status_valid { candidate_tasks += 1; }
                } else { finding(&mut findings, "task_rejected_by_model", "tasks", None, 1); }
            } else { finding(&mut findings, "task_required_value_missing", "tasks", None, 1); }
        }
        for column in ["created_at", "updated_at", "status_confidence_at"] {
            if has_column(&inventory, "tasks", column) {
                let mut statement = db.prepare(&format!("SELECT {column} FROM tasks")).map_err(error)?;
                let mut rows = statement.query([]).map_err(error)?;
                while let Some(row) = rows.next().map_err(error)? {
                    let value = text(row, 0, "tasks", column, &mut findings);
                    match value.as_deref().map(import_rules::utc_timestamp) {
                        Some(Ok(normalized)) => { if value.as_deref() != Some(&normalized) { normalized_utc_timestamps += 1; } }
                        _ => finding(&mut findings, "timestamp_requires_explicit_conversion", "tasks", Some(column), 1),
                    }
                }
            } else { finding(&mut findings, "missing_required_column", "tasks", Some(column), 1); }
        }
        provider_pairs(&db, &inventory, &mut findings)?;
    }
    let candidate_dependency_edges = dependencies(&db, &inventory, &task_ids, &mut findings)?;
    finding(&mut findings, "full_import_not_implemented", "destination", None, 1);
    if let Some(path) = snapshot_path { migration::write_snapshot(&db, path)?; }
    Ok(DryRun {
        schema: "frog.migration-dry-run.v1".into(),
        data_format_version: import_rules::DRY_RUN_DATA_FORMAT.parse()?, inventory, fields,
        findings: findings.into_iter().map(|((code, table, column), count)| Finding { code, table, column, count }).collect(),
        candidate_repositories, candidate_tasks, candidate_dependency_edges, normalized_utc_timestamps,
        destination_simulated: false, ready_to_apply: false,
    })
}

fn validates(command: Command) -> bool {
    crate::plan(Request { workspace_id: "migration-preflight".into(), actor: "migration-preflight".into(),
        operation_id: "preflight".into(), command }).is_ok()
}

fn text(row: &Row<'_>, index: usize, table: &str, column: &str, findings: &mut Findings) -> Option<String> {
    match row.get_ref(index) {
        Ok(ValueRef::Null) => None,
        Ok(ValueRef::Text(bytes)) => match std::str::from_utf8(bytes) {
            Ok(value) => Some(value.into()),
            Err(_) => { finding(findings, "invalid_text_encoding", table, Some(column), 1); None }
        },
        _ => { finding(findings, "invalid_storage_class", table, Some(column), 1); None }
    }
}

fn has_column(inventory: &Inventory, table: &str, column: &str) -> bool {
    inventory.tables.iter().any(|t| t.name == table && t.columns.iter().any(|c| c.name == column))
}

fn required(inventory: &Inventory, table: &str, columns: &[&str], findings: &mut Findings) -> bool {
    let mut complete = true;
    for column in columns {
        if !has_column(inventory, table, column) {
            finding(findings, "missing_required_column", table, Some(column), 1);
            complete = false;
        }
    }
    complete
}

fn provider_pairs(db: &Connection, inventory: &Inventory, findings: &mut Findings) -> Result<(), String> {
    if !has_column(inventory, "tasks", "source") && !has_column(inventory, "tasks", "external_id") { return Ok(()); }
    if !required(inventory, "tasks", &["source", "external_id"], findings) { return Ok(()); }
    let mut statement = db.prepare("SELECT source,external_id FROM tasks").map_err(error)?;
    let mut rows = statement.query([]).map_err(error)?;
    let mut seen = BTreeSet::new();
    while let Some(row) = rows.next().map_err(error)? {
        let source = text(row, 0, "tasks", "source", findings);
        let external = text(row, 1, "tasks", "external_id", findings);
        match (source, external) {
            (None, None) => (),
            (Some(source), Some(external)) if !source.is_empty() && !external.is_empty() => {
                if !seen.insert((source, external)) { finding(findings, "duplicate_provider_identity", "tasks", None, 1); }
            }
            _ => finding(findings, "incomplete_provider_identity", "tasks", None, 1),
        }
    }
    Ok(())
}

fn dependencies(db: &Connection, inventory: &Inventory, task_ids: &BTreeSet<String>, findings: &mut Findings) -> Result<u64, String> {
    if !required(inventory, "task_dependencies", &["task_slug", "depends_on_slug", "relation"], findings) { return Ok(0); }
    let mut statement = db.prepare("SELECT task_slug,depends_on_slug,relation FROM task_dependencies").map_err(error)?;
    let mut rows = statement.query([]).map_err(error)?;
    let mut edges = BTreeSet::new();
    let mut graph: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut indegrees: BTreeMap<String, usize> = BTreeMap::new();
    while let Some(row) = rows.next().map_err(error)? {
        let task = text(row, 0, "task_dependencies", "task_slug", findings);
        let dependency = text(row, 1, "task_dependencies", "depends_on_slug", findings);
        let relation = text(row, 2, "task_dependencies", "relation", findings);
        if relation.as_deref() != Some("depends_on") {
            finding(findings, "unsupported_dependency_relation", "task_dependencies", Some("relation"), 1);
            continue;
        }
        if let (Some(task), Some(dependency)) = (task, dependency) {
            if !task_ids.contains(&task) || !task_ids.contains(&dependency) {
                finding(findings, "dangling_dependency", "task_dependencies", None, 1);
            } else if task == dependency {
                finding(findings, "self_dependency", "task_dependencies", None, 1);
            } else if !validates(Command::AddDependency { task_id: task.clone(), depends_on: dependency.clone() }) {
                finding(findings, "dependency_rejected_by_model", "task_dependencies", None, 1);
            } else if !edges.insert((task.clone(), dependency.clone())) {
                finding(findings, "duplicate_dependency", "task_dependencies", None, 1);
            } else {
                indegrees.entry(task.clone()).or_default();
                *indegrees.entry(dependency.clone()).or_default() += 1;
                graph.entry(task).or_default().push(dependency);
            }
        } else { finding(findings, "dependency_identity_missing", "task_dependencies", None, 1); }
    }
    let mut queue: VecDeque<String> = indegrees.iter().filter(|(_, count)| **count == 0).map(|(id, _)| id.clone()).collect();
    let mut visited = 0;
    while let Some(id) = queue.pop_front() {
        visited += 1;
        for next in graph.get(&id).into_iter().flatten() {
            let count = indegrees.get_mut(next).unwrap();
            *count -= 1;
            if *count == 0 { queue.push_back(next.clone()); }
        }
    }
    if visited != indegrees.len() { finding(findings, "dependency_cycle_or_cycle_blocked", "task_dependencies", None, (indegrees.len() - visited) as u64); }
    Ok(edges.len() as u64)
}

fn error(error: rusqlite::Error) -> String { format!("migration assessment failed: {error}") }
