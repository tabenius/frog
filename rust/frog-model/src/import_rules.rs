//! Portable, conservative legacy mapping rules shared by native and WASM hosts.
use serde::{Deserialize, Serialize};

pub const DRY_RUN_DATA_FORMAT: &str = "20261020-4";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct FieldMapping {
    pub source_table: String,
    pub source_column: String,
    pub destination: Option<String>,
    pub rule: String,
}

/// A destination describes a candidate mapping, not implemented import writes.
/// Unrepresented fields always require an explicit schema/preservation decision.
pub fn field_mapping(table: &str, column: &str) -> FieldMapping {
    let candidate = match (table, column) {
        ("repos", "repo_key") => Some(("frog_model_repos.repo_id", "stable_identity")),
        ("repos", "name") => Some(("frog_model_repos.title", "copy")),
        ("tasks", "slug") => Some(("frog_model_tasks.task_id", "copy")),
        ("tasks", "repo_path") => Some(("frog_model_tasks.repo_id", "resolve_repo_identity")),
        ("tasks", "title") => Some(("frog_model_tasks.title", "copy")),
        ("tasks", "priority") => Some(("frog_model_tasks.priority", "priority_p0_to_p3")),
        ("tasks", "workflow_status") => Some(("frog_model_tasks.status", "exact_supported_status")),
        ("tasks", "assigned_agent") => Some(("frog_model_tasks.assignee", "copy")),
        ("tasks", "updated_at") => Some(("frog_model_tasks.updated_at", "preserve_timestamp")),
        ("tasks", "status_confidence_at") => Some(("frog_model_tasks.status_observed_at", "preserve_timestamp")),
        ("tasks", "why") => Some(("frog_model_task_details.why", "copy")),
        ("tasks", "what_text") => Some(("frog_model_task_details.what_text", "copy")),
        ("tasks", "roi_note") => Some(("frog_model_task_details.roi_note", "copy")),
        ("tasks", "source") => Some(("frog_model_provider_items.source", "provider_pair")),
        ("tasks", "external_id") => Some(("frog_model_provider_items.external_id", "provider_pair")),
        ("task_dependencies", "task_slug") => Some(("frog_model_dependencies.task_id", "copy")),
        ("task_dependencies", "depends_on_slug") => Some(("frog_model_dependencies.depends_on", "copy")),
        ("task_dependencies", "relation") => Some(("frog_model_dependencies", "depends_on_only")),
        _ => None,
    };
    FieldMapping {
        source_table: table.into(),
        source_column: column.into(),
        destination: candidate.map(|(destination, _)| destination.into()),
        rule: candidate.map_or("requires_schema_or_preservation", |(_, rule)| rule).into(),
    }
}

pub fn priority(value: &str) -> Result<u8, &'static str> {
    match value {
        "p0" => Ok(0),
        "p1" => Ok(1),
        "p2" => Ok(2),
        "p3" => Ok(3),
        _ => Err("unsupported_priority"),
    }
}

pub fn status(value: &str) -> Result<&str, &'static str> {
    match value {
        "todo" | "in_progress" | "review" | "published" | "done" | "cancelled" => Ok(value),
        "idea" | "blocked" => Err("status_requires_model_schema_extension"),
        _ => Err("unsupported_status"),
    }
}

/// Never silently derive a durable identity from a display name or local path.
pub fn repo_identity(value: Option<&str>) -> Result<&str, &'static str> {
    value.filter(|value| !value.is_empty()).ok_or("repository_identity_missing")
}

/// Preserve the instant; only canonical UTC and the equivalent +00:00 suffix
/// are understood. Other offsets require an explicit conversion adapter.
pub fn utc_timestamp(value: &str) -> Result<String, &'static str> {
    let invalid = "timestamp_requires_explicit_conversion";
    let body = value.strip_suffix('Z').or_else(|| value.strip_suffix("+00:00")).ok_or(invalid)?;
    if !body.is_ascii() || body.len() < 19 || &body[4..5] != "-" || &body[7..8] != "-"
        || &body[10..11] != "T" || &body[13..14] != ":" || &body[16..17] != ":" {
        return Err(invalid);
    }
    let date = format!("{}{}{}-1", &body[..4], &body[5..7], &body[8..10]);
    date.parse::<crate::data_format::DataFormatVersion>().map_err(|_| invalid)?;
    for (field, limit) in [(&body[11..13], 24), (&body[14..16], 60), (&body[17..19], 60)] {
        if !field.bytes().all(|c| c.is_ascii_digit()) || field.parse::<u8>().map_err(|_| invalid)? >= limit {
            return Err(invalid);
        }
    }
    let fraction = &body[19..];
    if !fraction.is_empty() && (!fraction.starts_with('.') || fraction.len() < 2 || fraction.len() > 10
        || !fraction[1..].bytes().all(|c| c.is_ascii_digit())) {
        return Err(invalid);
    }
    Ok(format!("{body}Z"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mapping_and_vocabulary_refuse_silent_loss() {
        assert_eq!(priority("p0"), Ok(0));
        assert_eq!(priority("p3"), Ok(3));
        assert!(priority("p4").is_err());
        assert!(priority("1").is_err());
        assert_eq!(status("done"), Ok("done"));
        assert!(status("idea").is_err());
        assert!(status("blocked").is_err());
        assert!(status("finished").is_err());
        assert!(repo_identity(None).is_err());
        assert!(repo_identity(Some("")).is_err());
        assert_eq!(repo_identity(Some("repo:frog")), Ok("repo:frog"));
        assert!(field_mapping("tasks", "unknown_metadata").destination.is_none());
        assert!(field_mapping("task_conflicts", "task_slug").destination.is_none());
        assert!(field_mapping("locks", "agent_name").destination.is_none());
        assert_eq!(field_mapping("tasks", "why").destination.as_deref(), Some("frog_model_task_details.why"));
    }

    #[test]
    fn timestamps_preserve_utc_and_refuse_unknown_instants() {
        assert_eq!(utc_timestamp("2026-10-20T12:34:56.123+00:00").unwrap(), "2026-10-20T12:34:56.123Z");
        assert_eq!(utc_timestamp("2024-02-29T00:00:00Z").unwrap(), "2024-02-29T00:00:00Z");
        for value in ["2026-10-20", "2026-02-29T00:00:00Z", "2026-10-20T24:00:00Z",
            "2026-10-20T00:60:00Z", "2026-10-20T00:00:60Z", "2026-10-20T00:00:00.Z",
            "2026-10-20T00:00:00+01:00", "2026-10-20T00:00:00", "garbageZ"] {
            assert!(utc_timestamp(value).is_err(), "{value}");
        }
    }
}
