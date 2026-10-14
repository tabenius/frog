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
