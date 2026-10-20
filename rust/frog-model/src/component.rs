wit_bindgen::generate!({ path:"wit", world:"model-planner" });
struct Component;
impl exports::frog::model::planner::Guest for Component {
    fn plan_operation(
        input: frog::model::types::Request,
    ) -> Result<frog::model::types::Plan, String> {
        use frog::model::types::{Command, Parameter, Plan, Statement};
        let command = match input.command {
            Command::RegisterRepo(value) => crate::Command::RegisterRepo {
                repo_id: value.repo_id,
                title: value.title,
            },
            Command::CreateTask(value) => crate::Command::CreateTask {
                repo_id: value.repo_id,
                task_id: value.task_id,
                title: value.title,
                priority: value.priority,
            },
            Command::ClaimTask(value) => crate::Command::ClaimTask {
                task_id: value.task_id,
                expected_revision: value.expected_revision,
            },
            Command::TransitionTask(value) => crate::Command::TransitionTask {
                task_id: value.task_id,
                expected_revision: value.expected_revision,
                status: value.status,
            },
            Command::CancelTask(value) => crate::Command::CancelTask {
                task_id: value.task_id,
                expected_revision: value.expected_revision,
            },
            Command::AddDependency(value) => crate::Command::AddDependency {
                task_id: value.task_id,
                depends_on: value.depends_on,
            },
            Command::RemoveDependency(value) => crate::Command::RemoveDependency {
                task_id: value.task_id,
                depends_on: value.depends_on,
            },
            Command::DescribeTask(task_id) => crate::Command::DescribeTask { task_id },
            Command::Board(value) => crate::Command::Board {
                repo_id: value.repo_id,
                limit: value.limit,
                after: value.after,
            },
            Command::Repositories(value) => crate::Command::Repositories { limit: value },
            Command::Events(value) => crate::Command::Events {
                after: value.after,
                limit: value.limit,
            },
            Command::EditTask(v) => crate::Command::EditTask {
                task_id: v.task_id,
                expected_revision: v.expected_revision,
                title: v.title,
                priority: v.priority,
            },
            Command::EditTaskFields(v) => crate::Command::EditTaskFields {
                task_id: v.task_id,
                expected_revision: v.expected_revision,
                fields: crate::TaskEditFields {
                    title: v.fields.title,
                    priority: v.fields.priority,
                    repo_id: v.fields.repo_id,
                    why: v.fields.why,
                    what_text: v.fields.what_text,
                    roi_note: v.fields.roi_note,
                },
            },
            Command::TaskDetails(task_id) => crate::Command::TaskDetails { task_id },
            Command::ProviderImport(v) => crate::Command::ProviderImport {
                source: v.source,
                external_id: v.external_id,
                repo_id: v.repo_id,
                task_id: v.task_id,
                title: v.title,
                priority: v.priority,
                status: v.status,
                expected_revision: v.expected_revision,
                why: v.why,
                what_text: v.what_text,
            },
            Command::ProviderOutbox(v) => crate::Command::ProviderOutbox {
                source: v.source,
                limit: v.limit,
                after: v.after,
            },
            Command::RecordVerification(v) => crate::Command::RecordVerification {
                task_id: v.task_id,
                expected_revision: v.expected_revision,
                target: v.target,
                passed: v.passed,
            },
            Command::Verifications(task_id) => crate::Command::Verifications { task_id },
            Command::AcquireLock(v) => crate::Command::AcquireLock {
                scope: v.scope,
                lease_id: v.lease_id,
                ttl_seconds: v.ttl_seconds,
            },
            Command::ReleaseLock(v) => crate::Command::ReleaseLock {
                scope: v.scope,
                lease_id: v.lease_id,
            },
            Command::Locks(v) => crate::Command::Locks {
                limit: v.limit,
                after: v.after,
            },
        };
        let result = crate::plan(crate::Request {
            workspace_id: input.workspace_id,
            actor: input.actor,
            operation_id: input.operation_id,
            command,
        });
        frog::model::tracing::counter(
            if result.is_ok() {
                "frog.model.plan.ok"
            } else {
                "frog.model.plan.rejected"
            },
            1,
        );
        result.map(|plan| Plan {
            schema: plan.schema,
            read_only: plan.read_only,
            result_kind: plan.result_kind,
            statements: plan
                .statements
                .into_iter()
                .map(|statement| Statement {
                    sql: statement.sql,
                    params: statement
                        .params
                        .into_iter()
                        .map(|value| match value {
                            serde_json::Value::Null => Parameter::Null,
                            serde_json::Value::String(value) => Parameter::Text(value),
                            serde_json::Value::Number(value) => {
                                Parameter::Integer(value.as_i64().unwrap())
                            }
                            _ => unreachable!(),
                        })
                        .collect(),
                })
                .collect(),
        })
    }
}
export!(Component);
