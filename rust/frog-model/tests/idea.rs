#![cfg(feature = "sqlite")]

use frog_model::{sqlite, Command, Request};
use rusqlite::Connection;
use serde_json::Value;

fn database() -> Connection {
    let mut db = Connection::open_in_memory().unwrap();
    db.pragma_update(None, "foreign_keys", "ON").unwrap();
    db.execute_batch(frog_model::MIGRATION).unwrap();
    db.execute_batch(frog_model::SERVICE_MIGRATION).unwrap();
    db.execute_batch("INSERT INTO frog_model_workspaces VALUES('work','Work'),('other','Other')")
        .unwrap();
    run(
        &mut db,
        "repo",
        Command::RegisterRepo {
            repo_id: "repo".into(),
            title: "Repo".into(),
        },
    );
    run(
        &mut db,
        "create",
        Command::CreateTask {
            initial_status: None,
            repo_id: "repo".into(),
            task_id: "task".into(),
            title: "Unstarted work".into(),
            priority: 1,
        },
    );
    db
}

fn request(operation: &str, command: Command) -> Request {
    Request {
        workspace_id: "work".into(),
        actor: "owner".into(),
        operation_id: operation.into(),
        command,
    }
}

fn run(db: &mut Connection, operation: &str, command: Command) -> Value {
    sqlite::execute(db, request(operation, command)).unwrap()
}

fn transition(status: &str, revision: u32) -> Command {
    Command::TransitionTask {
        task_id: "task".into(),
        expected_revision: revision,
        status: status.into(),
    }
}

fn task(db: &mut Connection) -> Value {
    run(
        db,
        "read",
        Command::DescribeTask {
            task_id: "task".into(),
        },
    )["items"][0]
        .clone()
}

#[test]
fn native_creation_is_atomic_in_idea_and_default_json_remains_compatible() {
    let mut db = database();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    let create = Command::CreateTask {
        repo_id: "repo".into(),
        task_id: "new-idea".into(),
        title: "New idea".into(),
        priority: 2,
        initial_status: Some("idea".into()),
    };
    let first = run(&mut db, "new-idea", create.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    assert_eq!(run(&mut db, "new-idea", create), first);
    let idea = run(
        &mut db,
        "read-idea",
        Command::DescribeTask {
            task_id: "new-idea".into(),
        },
    );
    assert_eq!(idea["items"][0]["status"], "idea");
    assert_eq!(idea["items"][0]["revision"], 0);
    let old_json = serde_json::json!({"kind":"create_task","repo_id":"repo","task_id":"old-client","title":"Old client","priority":2});
    let old: Command = serde_json::from_value(old_json.clone()).unwrap();
    assert_eq!(serde_json::to_value(&old).unwrap(), old_json);
    run(&mut db, "old-client", old);
    let ready = run(
        &mut db,
        "read-ready",
        Command::DescribeTask {
            task_id: "old-client".into(),
        },
    );
    assert_eq!(ready["items"][0]["status"], "todo");
    let bad = Command::CreateTask {
        repo_id: "repo".into(),
        task_id: "bad".into(),
        title: "Bad".into(),
        priority: 2,
        initial_status: Some("done".into()),
    };
    assert!(sqlite::execute(&mut db, request("bad", bad)).is_err());
}

#[test]
fn idea_requires_explicit_promotion_before_claiming_and_cannot_skip_workflow() {
    let mut db = database();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    let first = run(&mut db, "backlog", transition("idea", 0));
    assert_eq!(first["items"][0]["status"], "applied");
    assert_eq!(run(&mut db, "backlog", transition("idea", 0)), first);
    assert_eq!(task(&mut db)["status"], "idea");
    assert_eq!(task(&mut db)["lane"], "idea");
    let claim = Command::ClaimTask {
        task_id: "task".into(),
        expected_revision: 1,
    };
    assert_eq!(
        run(&mut db, "claim-idea", claim)["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        run(&mut db, "skip", transition("review", 1))["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        run(&mut db, "stale", transition("todo", 0))["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        run(&mut db, "promote", transition("todo", 1))["items"][0]["status"],
        "applied"
    );
    assert_eq!(task(&mut db)["status"], "todo");
    let claim = Command::ClaimTask {
        task_id: "task".into(),
        expected_revision: 2,
    };
    assert_eq!(
        run(&mut db, "claim-ready", claim)["items"][0]["status"],
        "applied"
    );
    assert_eq!(task(&mut db)["status"], "in_progress");
    assert_eq!(task(&mut db)["assignee"], "owner");
    assert_eq!(
        run(&mut db, "demote-active", transition("idea", 3))["items"][0]["status"],
        "conflict"
    );
}

#[test]
fn backlog_promotion_preserves_dependency_gates_and_tenant_isolation() {
    let mut db = database();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    run(
        &mut db,
        "create-prerequisite",
        Command::CreateTask {
            initial_status: None,
            repo_id: "repo".into(),
            task_id: "prerequisite".into(),
            title: "Unfinished prerequisite".into(),
            priority: 1,
        },
    );
    run(
        &mut db,
        "edge",
        Command::AddDependency {
            task_id: "task".into(),
            depends_on: "prerequisite".into(),
        },
    );
    run(&mut db, "backlog", transition("idea", 0));
    assert_eq!(task(&mut db)["lane"], "idea");
    assert_eq!(
        task(&mut db)["unmet_dependencies"],
        serde_json::json!(["prerequisite"])
    );
    let mut outsider = request("outsider", transition("todo", 1));
    outsider.workspace_id = "other".into();
    assert_eq!(
        sqlite::execute(&mut db, outsider).unwrap()["items"][0]["status"],
        "conflict"
    );
    run(&mut db, "promote", transition("todo", 1));
    assert_eq!(task(&mut db)["lane"], "blocked");
    assert_eq!(
        run(
            &mut db,
            "claim",
            Command::ClaimTask {
                task_id: "task".into(),
                expected_revision: 2
            }
        )["items"][0]["status"],
        "conflict"
    );
}

#[test]
fn providers_preserve_idea_and_owner_rules_apply_to_promotion_and_cancellation() {
    let mut db = database();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    run(
        &mut db,
        "provider",
        Command::ProviderImport {
            source: "dash".into(),
            external_id: "instance/task".into(),
            repo_id: "repo".into(),
            task_id: "imported".into(),
            title: "Source idea".into(),
            priority: 2,
            status: "idea".into(),
            expected_revision: None,
            why: None,
            what_text: None,
        },
    );
    let imported = run(
        &mut db,
        "read-import",
        Command::DescribeTask {
            task_id: "imported".into(),
        },
    );
    assert_eq!(imported["items"][0]["status"], "idea");
    run(&mut db, "backlog", transition("idea", 0));
    db.execute(
        "UPDATE frog_model_tasks SET assignee='owner' WHERE task_id='task'",
        [],
    )
    .unwrap();
    let mut peer = request("peer", transition("todo", 1));
    peer.actor = "peer".into();
    assert_eq!(
        sqlite::execute(&mut db, peer).unwrap()["items"][0]["status"],
        "conflict"
    );
    let cancel = Command::CancelTask {
        task_id: "task".into(),
        expected_revision: 1,
    };
    let mut peer = request("peer-cancel", cancel.clone());
    peer.actor = "peer".into();
    assert_eq!(
        sqlite::execute(&mut db, peer).unwrap()["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        run(&mut db, "owner-cancel", cancel)["items"][0]["status"],
        "applied"
    );
    assert_eq!(task(&mut db)["status"], "cancelled");
}

#[test]
fn preassigned_ideas_can_be_promoted_and_claimed_only_by_their_owner() {
    let mut db = database();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    run(&mut db, "backlog", transition("idea", 0));
    db.execute(
        "UPDATE frog_model_tasks SET assignee='owner' WHERE task_id='task'",
        [],
    )
    .unwrap();
    run(&mut db, "promote", transition("todo", 1));
    let claim = Command::ClaimTask {
        task_id: "task".into(),
        expected_revision: 2,
    };
    let mut peer = request("peer-claim", claim.clone());
    peer.actor = "peer".into();
    assert_eq!(
        sqlite::execute(&mut db, peer).unwrap()["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        run(&mut db, "owner-claim", claim)["items"][0]["status"],
        "applied"
    );
    assert_eq!(task(&mut db)["assignee"], "owner");
    assert_eq!(task(&mut db)["status"], "in_progress");
}

#[test]
fn upgrade_preserves_existing_fields_children_receipts_and_foreign_keys() {
    let mut db = database();
    run(
        &mut db,
        "details",
        Command::EditTaskFields {
            task_id: "task".into(),
            expected_revision: 0,
            fields: frog_model::TaskEditFields {
                why: Some("Original reason".into()),
                ..Default::default()
            },
        },
    );
    run(
        &mut db,
        "verify",
        Command::RecordVerification {
            task_id: "task".into(),
            expected_revision: 1,
            target: "unit".into(),
            passed: true,
        },
    );
    let before = task(&mut db);
    let receipts: i64 = db
        .query_row("SELECT count(*) FROM frog_model_operations", [], |r| {
            r.get(0)
        })
        .unwrap();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    assert_eq!(task(&mut db), before);
    assert_eq!(
        db.query_row("SELECT count(*) FROM frog_model_operations", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        receipts
    );
    assert_eq!(
        db.query_row("SELECT why FROM frog_model_task_details", [], |r| r
            .get::<_, String>(0))
            .unwrap(),
        "Original reason"
    );
    assert_eq!(
        db.query_row("SELECT count(*) FROM frog_model_verifications", [], |r| r
            .get::<_, i64>(
            0
        ))
        .unwrap(),
        1
    );
    assert_eq!(
        db.query_row("SELECT version FROM frog_model_schema", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        2
    );
    assert_eq!(
        db.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert!(db
        .prepare("PRAGMA foreign_key_check")
        .unwrap()
        .query([])
        .unwrap()
        .next()
        .unwrap()
        .is_none());
}

#[test]
fn old_schemas_require_explicit_upgrade_and_failed_upgrades_restore_enforcement() {
    let mut db = database();
    let before = task(&mut db);
    assert!(
        sqlite::execute(&mut db, request("backlog", transition("idea", 0)))
            .unwrap_err()
            .contains("explicit")
    );
    db.execute_batch("CREATE TABLE frog_model_tasks_idea(existing TEXT); INSERT INTO frog_model_tasks_idea VALUES('retained')").unwrap();
    assert!(sqlite::upgrade_idea_schema(&mut db, "frog_model_").is_err());
    assert_eq!(task(&mut db), before);
    assert_eq!(
        db.query_row("SELECT version FROM frog_model_schema", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        db.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        db.query_row("SELECT existing FROM frog_model_tasks_idea", [], |r| r
            .get::<_, String>(
            0
        ))
        .unwrap(),
        "retained"
    );
    db.execute_batch("DROP TABLE frog_model_tasks_idea; CREATE TRIGGER custom AFTER INSERT ON frog_model_tasks BEGIN SELECT 1; END").unwrap();
    assert!(sqlite::upgrade_idea_schema(&mut db, "frog_model_")
        .unwrap_err()
        .contains("customized"));
    assert_eq!(task(&mut db), before);
    assert_eq!(
        db.query_row("PRAGMA foreign_keys", [], |r| r.get::<_, i64>(0))
            .unwrap(),
        1
    );
    db.execute_batch("DROP TRIGGER custom; BEGIN").unwrap();
    assert!(sqlite::upgrade_idea_schema(&mut db, "frog_model_").is_err());
    db.execute_batch("ROLLBACK").unwrap();
}

#[test]
fn explicit_upgrade_supports_custom_prefixes() {
    let mut db = Connection::open_in_memory().unwrap();
    db.pragma_update(None, "foreign_keys", "ON").unwrap();
    db.execute_batch(&frog_model::MIGRATION.replace("frog_model_", "pilot_"))
        .unwrap();
    sqlite::upgrade_idea_schema(&mut db, "pilot_").unwrap();
    assert_eq!(
        db.query_row("SELECT version FROM pilot_schema", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        2
    );
    assert!(db.prepare("SELECT * FROM frog_model_tasks").is_err());
    assert!(sqlite::upgrade_idea_schema(&mut db, "bad-prefix;").is_err());
}
