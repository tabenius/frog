use frog_model::{Command, Request};
use rusqlite::Connection;

fn database() -> Connection {
    let connection = Connection::open_in_memory().unwrap();
    connection.execute_batch("PRAGMA foreign_keys=ON;").unwrap();
    connection.execute_batch(frog_model::MIGRATION).unwrap();
    connection
        .execute_batch(frog_model::SERVICE_MIGRATION)
        .unwrap();
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
fn items(db: &mut Connection, ws: &str, command: Command) -> Vec<serde_json::Value> {
    execute(db, ws, "viewer", "query", command)["items"]
        .as_array()
        .cloned()
        .unwrap_or_default()
}

#[test]
fn editing_metadata_is_atomic_and_omitted_fields_are_preserved() {
    let mut db = database();
    seed(&mut db, "alpha");
    let fields = frog_model::TaskEditFields {
        why: Some("Reason".into()),
        what_text: Some("Work".into()),
        roi_note: Some("ROI".into()),
        ..Default::default()
    };
    let edit = Command::EditTaskFields {
        task_id: "one".into(),
        expected_revision: 0,
        fields,
    };
    let first = execute(&mut db, "alpha", "owner", "details", edit.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    assert_eq!(execute(&mut db, "alpha", "owner", "details", edit), first);
    let stale = Command::EditTaskFields {
        task_id: "one".into(),
        expected_revision: 0,
        fields: frog_model::TaskEditFields {
            why: Some("Lost update".into()),
            ..Default::default()
        },
    };
    assert_eq!(
        execute(&mut db, "alpha", "owner", "stale-details", stale)["items"][0]["status"],
        "conflict"
    );
    let update = Command::EditTaskFields {
        task_id: "one".into(),
        expected_revision: 1,
        fields: frog_model::TaskEditFields {
            why: Some(String::new()),
            ..Default::default()
        },
    };
    assert_eq!(
        execute(&mut db, "alpha", "owner", "clear-why", update)["items"][0]["status"],
        "applied"
    );
    let details = items(
        &mut db,
        "alpha",
        Command::TaskDetails {
            task_id: "one".into(),
        },
    );
    assert_eq!(details[0]["why"], "");
    assert_eq!(details[0]["what_text"], "Work");
    assert_eq!(details[0]["roi_note"], "ROI");
    assert_eq!(
        items(
            &mut db,
            "alpha",
            Command::DescribeTask {
                task_id: "one".into()
            }
        )[0]["status"],
        "todo"
    );
    assert!(items(
        &mut db,
        "beta",
        Command::TaskDetails {
            task_id: "one".into()
        }
    )
    .is_empty());
}

#[test]
fn provider_import_is_revision_checked_and_preserves_external_identity() {
    let mut db = database();
    seed(&mut db, "alpha");
    let import = Command::ProviderImport {
        source: "jira".into(),
        external_id: "Project / 42".into(),
        repo_id: "frog".into(),
        task_id: "jira:42".into(),
        title: "Imported".into(),
        priority: 2,
        status: "todo".into(),
        expected_revision: None,
        why: Some("Imported reason".into()),
        what_text: None,
    };
    let first = execute(&mut db, "alpha", "owner", "import", import.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    assert_eq!(execute(&mut db, "alpha", "owner", "import", import), first);
    let update = Command::ProviderImport {
        source: "jira".into(),
        external_id: "Project / 42".into(),
        repo_id: "frog".into(),
        task_id: "jira:42".into(),
        title: "Updated".into(),
        priority: 1,
        status: "done".into(),
        expected_revision: Some(0),
        why: None,
        what_text: Some("Imported work".into()),
    };
    assert_eq!(
        execute(&mut db, "alpha", "owner", "update", update.clone())["items"][0]["status"],
        "applied"
    );
    assert_eq!(
        execute(&mut db, "alpha", "owner", "stale-import", update)["items"][0]["status"],
        "conflict"
    );
    let rows = items(
        &mut db,
        "alpha",
        Command::ProviderOutbox {
            source: "jira".into(),
            limit: 200,
            after: None,
        },
    );
    assert_eq!(rows[0]["external_id"], "Project / 42");
    assert_eq!(rows[0]["title"], "Updated");
    assert_eq!(rows[0]["revision"], 1);
    let details = items(
        &mut db,
        "alpha",
        Command::TaskDetails {
            task_id: "jira:42".into(),
        },
    );
    assert_eq!(details[0]["why"], "Imported reason");
    assert_eq!(details[0]["what_text"], "Imported work");
    assert!(items(
        &mut db,
        "beta",
        Command::ProviderOutbox {
            source: "jira".into(),
            limit: 200,
            after: None
        }
    )
    .is_empty());
    let count: i64 = db
        .query_row(
            "SELECT count(*) FROM frog_model_events WHERE kind='provider.imported'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 2);
}

#[test]
fn verification_receipts_are_revision_bound_and_replay_safe() {
    let mut db = database();
    seed(&mut db, "alpha");
    let command = Command::RecordVerification {
        task_id: "one".into(),
        expected_revision: 0,
        target: "test".into(),
        passed: true,
    };
    let first = execute(&mut db, "alpha", "runner", "verified", command.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    assert_eq!(
        execute(&mut db, "alpha", "runner", "verified", command.clone()),
        first
    );
    execute(
        &mut db,
        "alpha",
        "owner",
        "edit",
        Command::EditTask {
            task_id: "one".into(),
            expected_revision: 0,
            title: Some("Changed".into()),
            priority: None,
        },
    );
    assert_eq!(
        execute(&mut db, "alpha", "runner", "stale-verification", command)["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        items(
            &mut db,
            "alpha",
            Command::Verifications {
                task_id: "one".into()
            }
        )[0]["revision"],
        0
    );
    let count: i64 = db
        .query_row(
            "SELECT count(*) FROM frog_model_events WHERE kind='task.verified'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 1);
}

#[test]
fn leases_exclude_competitors_and_fence_stale_release() {
    let mut db = database();
    seed(&mut db, "alpha");
    let acquire = Command::AcquireLock {
        scope: "frog".into(),
        lease_id: "lease-a".into(),
        ttl_seconds: 60,
    };
    let first = execute(&mut db, "alpha", "alice", "acquire", acquire.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    let expiry: i64 = db
        .query_row("SELECT expires_at FROM frog_model_locks", [], |r| r.get(0))
        .unwrap();
    assert_eq!(
        execute(&mut db, "alpha", "alice", "acquire", acquire),
        first
    );
    assert_eq!(
        db.query_row("SELECT expires_at FROM frog_model_locks", [], |r| r
            .get::<_, i64>(0))
            .unwrap(),
        expiry
    );
    let competitor = Command::AcquireLock {
        scope: "frog".into(),
        lease_id: "lease-b".into(),
        ttl_seconds: 60,
    };
    assert_eq!(
        execute(&mut db, "alpha", "bob", "compete", competitor.clone())["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "bob",
            "wrong-owner",
            Command::ReleaseLock {
                scope: "frog".into(),
                lease_id: "lease-a".into()
            }
        )["items"][0]["status"],
        "conflict"
    );
    db.execute("UPDATE frog_model_locks SET expires_at=unixepoch()-1", [])
        .unwrap();
    assert!(items(
        &mut db,
        "alpha",
        Command::Locks {
            limit: 200,
            after: None
        }
    )
    .is_empty());
    assert_eq!(
        execute(&mut db, "alpha", "bob", "takeover", competitor)["items"][0]["status"],
        "applied"
    );
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "stale-release",
            Command::ReleaseLock {
                scope: "frog".into(),
                lease_id: "lease-a".into()
            }
        )["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "bob",
            "release",
            Command::ReleaseLock {
                scope: "frog".into(),
                lease_id: "lease-b".into()
            }
        )["items"][0]["status"],
        "applied"
    );
    assert!(items(
        &mut db,
        "alpha",
        Command::Locks {
            limit: 200,
            after: None
        }
    )
    .is_empty());
}

#[test]
fn editing_uses_revision_and_preserves_status_with_idempotent_events() {
    let mut db = database();
    seed(&mut db, "alpha");
    let edit = Command::EditTask {
        task_id: "one".into(),
        expected_revision: 0,
        title: Some("Edited".into()),
        priority: Some(2),
    };
    let first = execute(&mut db, "alpha", "owner", "edit", edit.clone());
    assert_eq!(first["items"][0]["status"], "applied");
    assert_eq!(execute(&mut db, "alpha", "owner", "edit", edit), first);
    let stale = execute(
        &mut db,
        "alpha",
        "owner",
        "stale",
        Command::EditTask {
            task_id: "one".into(),
            expected_revision: 0,
            title: Some("Lost update".into()),
            priority: None,
        },
    );
    assert_eq!(stale["items"][0]["status"], "conflict");
    let rows = items(
        &mut db,
        "alpha",
        Command::DescribeTask {
            task_id: "one".into(),
        },
    );
    assert_eq!(rows[0]["title"], "Edited");
    assert_eq!(rows[0]["priority"], 2);
    assert_eq!(rows[0]["status"], "todo");
    assert_eq!(rows[0]["revision"], 1);
    let count: i64 = db
        .query_row(
            "SELECT count(*) FROM frog_model_events WHERE kind='task.edited'",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(count, 1);
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
    let count: i64 = db
        .query_row("SELECT COUNT(*) FROM frog_model_events WHERE workspace_id='alpha' AND kind='task.claimed'", [], |row| row.get(0))
        .unwrap();
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
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "bob",
            "foreign",
            Command::TransitionTask {
                task_id: "one".into(),
                expected_revision: 1,
                status: "review".into()
            }
        )["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "leap",
            Command::TransitionTask {
                task_id: "one".into(),
                expected_revision: 1,
                status: "done".into()
            }
        )["items"][0]["status"],
        "conflict"
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
        assert_eq!(result["items"][0]["status"], "applied", "{status}");
        let lane = items(
            &mut db,
            "alpha",
            Command::Board {
                repo_id: None,
                limit: 10,
                after: None,
            },
        )[0]["lane"]
            .clone();
        assert_eq!(
            lane,
            if status == "done" {
                "done"
            } else {
                "in_progress"
            }
        );
    }
    // Done is terminal: no further reporting transition, but cancellation is refused too.
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "after-done",
            Command::CancelTask {
                task_id: "one".into(),
                expected_revision: 4
            }
        )["items"][0]["status"],
        "conflict"
    );
}

#[test]
fn dependencies_gate_claims_and_are_reported_on_the_board() {
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
    // A dependency that does not exist is refused, not silently recorded.
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "ghost",
            Command::AddDependency {
                task_id: "one".into(),
                depends_on: "missing".into()
            }
        )["items"][0]["status"],
        "conflict"
    );
    let self_dep = Command::AddDependency {
        task_id: "one".into(),
        depends_on: "one".into(),
    };
    assert!(frog_model::plan(request("alpha", "alice", "self", self_dep)).is_err());

    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "depend",
            Command::AddDependency {
                task_id: "one".into(),
                depends_on: "two".into()
            }
        )["items"][0]["status"],
        "applied"
    );
    let board = items(
        &mut db,
        "alpha",
        Command::Board {
            repo_id: None,
            limit: 10,
            after: None,
        },
    );
    let one = board.iter().find(|task| task["id"] == "one").unwrap();
    assert_eq!(one["lane"], "blocked");
    assert_eq!(one["unmet_dependencies"], serde_json::json!(["two"]));
    assert_eq!(one["dependencies"], serde_json::json!(["two"]));
    let two = board.iter().find(|task| task["id"] == "two").unwrap();
    assert_eq!(two["blocks"], serde_json::json!(["one"]));
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "blocked-claim",
            Command::ClaimTask {
                task_id: "one".into(),
                expected_revision: 0
            }
        )["items"][0]["status"],
        "conflict"
    );

    // Completing the dependency unblocks the dependent claim.
    execute(
        &mut db,
        "alpha",
        "alice",
        "claim-two",
        Command::ClaimTask {
            task_id: "two".into(),
            expected_revision: 0,
        },
    );
    for (revision, status) in [(1, "review"), (2, "published"), (3, "done")] {
        execute(
            &mut db,
            "alpha",
            "alice",
            status,
            Command::TransitionTask {
                task_id: "two".into(),
                expected_revision: revision,
                status: status.into(),
            },
        );
    }
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "claim-one",
            Command::ClaimTask {
                task_id: "one".into(),
                expected_revision: 0
            }
        )["items"][0]["status"],
        "applied"
    );

    // Removing a recorded edge reports what it changed; a second removal of an
    // edge that is already gone applies as a no-op and records no event.
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "unlink",
            Command::RemoveDependency {
                task_id: "one".into(),
                depends_on: "two".into()
            }
        )["items"][0]["status"],
        "applied"
    );
    let after_first: i64 = db.query_row("SELECT COUNT(*) FROM frog_model_events WHERE workspace_id='alpha' AND kind='task.dependency_removed'", [], |row| row.get(0)).unwrap();
    assert_eq!(after_first, 1);
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "unlink-again",
            Command::RemoveDependency {
                task_id: "one".into(),
                depends_on: "two".into()
            }
        )["items"][0]["status"],
        "applied"
    );
    let after_second: i64 = db.query_row("SELECT COUNT(*) FROM frog_model_events WHERE workspace_id='alpha' AND kind='task.dependency_removed'", [], |row| row.get(0)).unwrap();
    assert_eq!(after_second, 1);
    assert_eq!(
        items(
            &mut db,
            "alpha",
            Command::Board {
                repo_id: None,
                limit: 10,
                after: None
            }
        )
        .iter()
        .find(|task| task["id"] == "one")
        .unwrap()["unmet_dependencies"],
        serde_json::json!([])
    );
}

#[test]
fn cancellation_is_owner_or_unassigned_only_and_terminal() {
    let mut db = database();
    seed(&mut db, "alpha");
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "cancel-claim",
            Command::ClaimTask {
                task_id: "one".into(),
                expected_revision: 0
            }
        )["items"][0]["status"],
        "applied"
    );
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "bob",
            "cancel-foreign",
            Command::CancelTask {
                task_id: "one".into(),
                expected_revision: 1
            }
        )["items"][0]["status"],
        "conflict"
    );
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "cancel",
            Command::CancelTask {
                task_id: "one".into(),
                expected_revision: 1
            }
        )["items"][0]["status"],
        "applied"
    );
    let board = items(
        &mut db,
        "alpha",
        Command::Board {
            repo_id: None,
            limit: 10,
            after: None,
        },
    );
    assert_eq!(board[0]["status"], "cancelled");
    assert_eq!(board[0]["lane"], "done");
}

#[test]
fn board_pagination_is_keyset_stable_and_filtered() {
    let mut db = database();
    execute(
        &mut db,
        "alpha",
        "owner",
        "repo",
        Command::RegisterRepo {
            repo_id: "frog".into(),
            title: "Frog".into(),
        },
    );
    execute(
        &mut db,
        "alpha",
        "owner",
        "other",
        Command::RegisterRepo {
            repo_id: "nostoi".into(),
            title: "Nostoi".into(),
        },
    );
    for (id, repo, priority) in [
        ("a", "frog", 0),
        ("b", "frog", 0),
        ("c", "frog", 2),
        ("d", "nostoi", 1),
    ] {
        execute(
            &mut db,
            "alpha",
            "owner",
            &format!("create-{id}"),
            Command::CreateTask {
                repo_id: repo.into(),
                task_id: id.into(),
                title: id.into(),
                priority,
            },
        );
    }
    let mut seen = Vec::new();
    let mut cursor: Option<String> = None;
    loop {
        let page = items(
            &mut db,
            "alpha",
            Command::Board {
                repo_id: None,
                limit: 1,
                after: cursor.clone(),
            },
        );
        if page.is_empty() {
            break;
        }
        seen.push(page[0]["id"].as_str().unwrap().to_string());
        cursor = Some(format!(
            "{}:{}",
            page[0]["priority"],
            page[0]["id"].as_str().unwrap()
        ));
    }
    assert_eq!(seen, vec!["a", "b", "d", "c"]);
    let filtered = items(
        &mut db,
        "alpha",
        Command::Board {
            repo_id: Some("nostoi".into()),
            limit: 10,
            after: None,
        },
    );
    assert_eq!(filtered.len(), 1);
    assert_eq!(filtered[0]["repository"], "nostoi");
    assert_eq!(
        items(
            &mut db,
            "alpha",
            Command::DescribeTask {
                task_id: "c".into()
            }
        )[0]["id"],
        "c"
    );
    assert_eq!(
        items(
            &mut db,
            "alpha",
            Command::DescribeTask {
                task_id: "absent".into()
            }
        )
        .len(),
        0
    );
    // Board results carry the renderer-facing projection, not private storage.
    let row = &items(
        &mut db,
        "alpha",
        Command::Board {
            repo_id: None,
            limit: 10,
            after: None,
        },
    )[0];
    for field in [
        "id",
        "repository",
        "title",
        "priority",
        "status",
        "assignee",
        "revision",
        "updated_at",
        "status_observed_at",
        "lane",
        "unmet_dependencies",
        "dependencies",
        "blocks",
    ] {
        assert!(row.get(field).is_some(), "missing {field}");
    }
    assert!(row.as_object().unwrap().len() <= 13);
}

#[test]
fn validation_rejects_unknown_fields_bad_priority_and_unbounded_queries() {
    assert!(serde_json::from_str::<Request>(r#"{"workspace_id":"alpha","actor":"a","operation_id":"x","command":{"kind":"repositories","limit":1},"sql":"DROP TABLE tasks"}"#).is_err());
    for command in [
        Command::Board {
            repo_id: None,
            limit: 0,
            after: None,
        },
        Command::Board {
            repo_id: None,
            limit: 10,
            after: Some("9:nope".into()),
        },
        Command::Board {
            repo_id: None,
            limit: 10,
            after: Some("0".into()),
        },
        Command::Events {
            after: "1; DROP".into(),
            limit: 10,
        },
        Command::CreateTask {
            repo_id: "frog".into(),
            task_id: "one".into(),
            title: "x".into(),
            priority: 4,
        },
        Command::TransitionTask {
            task_id: "one".into(),
            expected_revision: 0,
            status: "in_progress".into(),
        },
    ] {
        assert!(frog_model::plan(request("alpha", "alice", "x", command)).is_err());
    }
}

#[test]
fn cross_workspace_dependency_edges_are_impossible() {
    let mut db = database();
    seed(&mut db, "alpha");
    seed(&mut db, "beta");
    // A task ID that exists only in beta cannot be linked from alpha.
    assert_eq!(
        execute(
            &mut db,
            "alpha",
            "alice",
            "cross",
            Command::AddDependency {
                task_id: "one".into(),
                depends_on: "absent-in-alpha".into()
            }
        )["items"][0]["status"],
        "conflict"
    );
    let edges: i64 = db
        .query_row("SELECT COUNT(*) FROM frog_model_dependencies", [], |row| {
            row.get(0)
        })
        .unwrap();
    assert_eq!(edges, 0);
}
