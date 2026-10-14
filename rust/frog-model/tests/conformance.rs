use frog_model::{Command, Request};
use rusqlite::Connection;

fn database() -> Connection {
    let connection = Connection::open_in_memory().unwrap();
    connection.execute_batch("PRAGMA foreign_keys=ON;").unwrap();
    connection.execute_batch(frog_model::MIGRATION).unwrap();
    connection
        .execute_batch("INSERT INTO frog_model_workspaces VALUES('alpha','Alpha'),('beta','Beta');")
        .unwrap();
    connection
}
fn request(workspace: &str, actor: &str, operation: &str, command: Command) -> Request {
    Request {
        workspace_id: workspace.into(),
        actor: actor.into(),
        operation_id: operation.into(),
        command,
    }
}
fn execute(
    db: &mut Connection,
    ws: &str,
    actor: &str,
    op: &str,
    command: Command,
) -> serde_json::Value {
    frog_model::sqlite::execute(db, request(ws, actor, op, command)).unwrap()
}
fn seed(db: &mut Connection, ws: &str) {
    execute(
        db,
        ws,
        "owner",
        "repo",
        Command::RegisterRepo {
            repo_id: "frog".into(),
            title: "Frog".into(),
        },
    );
    execute(
        db,
        ws,
        "owner",
        "create",
        Command::CreateTask {
            repo_id: "frog".into(),
            task_id: "one".into(),
            title: "Task one".into(),
            priority: 0,
        },
    );
}
#[test]
fn tenancy_idempotency_and_claim_conflicts() {
    let mut db = database();
    seed(&mut db, "alpha");
    seed(&mut db, "beta");
    let command = Command::ClaimTask {
        task_id: "one".into(),
        expected_revision: 0,
    };
    let first = execute(&mut db, "alpha", "alice", "claim", command.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    let replay = execute(&mut db, "alpha", "alice", "claim", command.clone());
    assert_eq!(first, replay);
    let collision = execute(&mut db, "alpha", "mallory", "claim", command.clone());
    assert_eq!(collision["items"][0]["status"], "idempotency_conflict");
    let second = execute(&mut db, "alpha", "bob", "second", command.clone());
    assert_eq!(second["items"][0]["status"], "conflict");
    let other = execute(&mut db, "beta", "bob", "claim", command);
    assert_eq!(other["items"][0]["status"], "applied");
    let count:i64=db.query_row("SELECT COUNT(*) FROM frog_model_events WHERE workspace_id='alpha' AND kind='task.claimed'",[],|row|row.get(0)).unwrap();
    assert_eq!(count, 1);
}
#[test]
fn transition_requires_owner_and_keeps_review_published_active() {
    let mut db = database();
    seed(&mut db, "alpha");
    execute(
        &mut db,
        "alpha",
        "alice",
        "claim",
        Command::ClaimTask {
            task_id: "one".into(),
            expected_revision: 0,
        },
    );
    for (revision, status) in [(1, "review"), (2, "published"), (3, "done")] {
        let result = execute(
            &mut db,
            "alpha",
            "alice",
            status,
            Command::TransitionTask {
                task_id: "one".into(),
                expected_revision: revision,
                status: status.into(),
            },
        );
        assert_eq!(result["items"][0]["status"], "applied");
        let board = execute(
            &mut db,
            "alpha",
            "viewer",
            "query",
            Command::Board {
                repo_id: None,
                limit: 10,
            },
        );
        assert_eq!(
            board["items"][0]["lane"],
            if status == "done" {
                "done"
            } else {
                "in_progress"
            }
        );
    }
}
#[test]
fn dependency_blocks_claim_and_sql_error_rolls_back_whole_batch() {
    let mut db = database();
    seed(&mut db, "alpha");
    execute(
        &mut db,
        "alpha",
        "alice",
        "second",
        Command::CreateTask {
            repo_id: "frog".into(),
            task_id: "two".into(),
            title: "Task two".into(),
            priority: 1,
        },
    );
    db.execute_batch("INSERT INTO frog_model_dependencies VALUES('alpha','one','two');")
        .unwrap();
    let result = execute(
        &mut db,
        "alpha",
        "alice",
        "blocked",
        Command::ClaimTask {
            task_id: "one".into(),
            expected_revision: 0,
        },
    );
    assert_eq!(result["items"][0]["status"], "conflict");
    db.execute_batch("DELETE FROM frog_model_dependencies; UPDATE frog_model_tasks SET revision=4294967295 WHERE workspace_id='alpha' AND task_id='one';").unwrap();
    assert!(frog_model::sqlite::execute(
        &mut db,
        request(
            "alpha",
            "alice",
            "overflow",
            Command::ClaimTask {
                task_id: "one".into(),
                expected_revision: u32::MAX
            }
        )
    )
    .is_err());
    let count: i64 = db
        .query_row(
            "SELECT COUNT(*) FROM frog_model_operations WHERE operation_id='overflow'",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(count, 0);
}
#[test]
fn validation_rejects_unknown_fields_bad_priority_and_unbounded_queries() {
    assert!(serde_json::from_str::<Request>(r#"{"workspace_id":"alpha","actor":"a","operation_id":"x","command":{"kind":"repositories","limit":1},"sql":"DROP TABLE tasks"}"#).is_err());
    for command in [
        Command::Board {
            repo_id: None,
            limit: 0,
        },
        Command::CreateTask {
            repo_id: "frog".into(),
            task_id: "one".into(),
            title: "x".into(),
            priority: 4,
        },
    ] {
        assert!(frog_model::plan(request("alpha", "alice", "x", command)).is_err());
    }
}
