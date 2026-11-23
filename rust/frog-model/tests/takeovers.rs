#![cfg(feature = "sqlite")]
use frog_model::{Command, Request};
use rusqlite::Connection;
use serde_json::Value;

fn run(db: &mut Connection, actor: &str, op: &str, command: Command) -> Value {
    frog_model::sqlite::execute(db, Request {
        workspace_id: "alpha".into(), actor: actor.into(), operation_id: op.into(), command,
    }).unwrap()
}

fn fixture(status: &str) -> Connection {
    let mut db = Connection::open_in_memory().unwrap();
    db.execute_batch("PRAGMA foreign_keys=ON").unwrap();
    db.execute_batch(frog_model::MIGRATION).unwrap();
    db.execute_batch(frog_model::SERVICE_MIGRATION).unwrap();
    db.execute_batch("INSERT INTO frog_model_workspaces VALUES('alpha','Alpha')").unwrap();
    run(&mut db, "owner", "repo", Command::RegisterRepo { repo_id:"frog".into(),title:"Frog".into() });
    for name in ["one", "two"] {
        run(&mut db, "owner", name, Command::CreateTask {
            repo_id:"frog".into(),task_id:name.into(),title:name.into(),priority:1,
        });
    }
    // A legacy-import fixture, never a mutation of an actual workspace store.
    db.execute("UPDATE frog_model_tasks SET assignee='old-agent',revision=7,status=?1,status_observed_at='2000-01-01'",[status]).unwrap();
    run(&mut db, "local-client", "lease", Command::AcquireLock {
        scope:"frog".into(),lease_id:"owned-lease".into(),ttl_seconds:3600,
    });
    db
}

fn takeover(task: &str) -> Command {
    Command::TakeOverTask { task_id:task.into(),expected_revision:7,
        expected_assignee:Some("old-agent".into()),lease_id:"owned-lease".into(),
        reason:"Owner approved inactive assignment recovery".into() }
}

fn task(db: &mut Connection, name: &str) -> Value {
    run(db,"viewer","read",Command::DescribeTask{task_id:name.into()})["items"][0].clone()
}

#[test]
fn takeover_is_audited_revision_checked_and_replay_safe() {
    let mut db=fixture("review");
    let first=run(&mut db,"local-client","recover",takeover("one"));
    assert_eq!(first["items"][0]["status"],"applied");
    assert_eq!(run(&mut db,"local-client","recover",takeover("one")),first);
    let row=task(&mut db,"one");
    assert_eq!(row["revision"],8);
    assert_eq!(row["assignee"],"local-client");
    assert_eq!(row["status"],"review");
    assert_eq!(row["status_observed_at"],"2000-01-01");
    let audit=run(&mut db,"viewer","history",Command::TaskTakeovers {
        task_id:"one".into(),limit:10,after:None,
    });
    assert_eq!(audit["items"].as_array().unwrap().len(),1);
    assert_eq!(audit["items"][0]["previous_assignee"],"old-agent");
    assert_eq!(audit["items"][0]["actor"],"local-client");
    assert_eq!(audit["items"][0]["revision"],8);
    // Reusing an operation ID with another otherwise eligible task must not
    // mutate it or produce an audit/event under an unrelated receipt.
    assert_eq!(run(&mut db,"local-client","recover",takeover("two"))["items"][0]["status"],"idempotency_conflict");
    assert_eq!(task(&mut db,"two")["assignee"],"old-agent");
    assert_eq!(task(&mut db,"two")["revision"],7);
}

#[test]
fn takeover_refuses_stale_owners_leases_terminals_dependencies_and_other_work() {
    for case in ["revision","assignee","expired","foreign","competing","done","cancelled","dependency"] {
        let mut db=fixture(if case=="done" || case=="cancelled" {case} else {"in_progress"});
        let mut command=takeover("one");
        match case {
            "revision" => if let Command::TakeOverTask{expected_revision,..}=&mut command {*expected_revision=6;},
            "assignee" => if let Command::TakeOverTask{expected_assignee,..}=&mut command {*expected_assignee=Some("another-agent".into());},
            "expired" => {db.execute("UPDATE frog_model_locks SET expires_at=unixepoch()-1",[]).unwrap();},
            "foreign" => {db.execute("UPDATE frog_model_locks SET owner='other'",[]).unwrap();},
            "competing" => {run(&mut db,"other","other-lease",Command::AcquireLock{scope:"opaque-file-scope".into(),lease_id:"peer-lease".into(),ttl_seconds:60});},
            "dependency" => {run(&mut db,"owner","dependency",Command::AddDependency{task_id:"one".into(),depends_on:"two".into()});},
            _ => (),
        }
        assert_eq!(run(&mut db,"local-client","recover",command)["items"][0]["status"],"conflict","{case}");
        assert_eq!(task(&mut db,"one")["assignee"],"old-agent","{case}");
        let count:i64=db.query_row("SELECT count(*) FROM frog_model_task_takeovers",[],|r|r.get(0)).unwrap();
        assert_eq!(count,0,"{case}");
    }
}

#[test]
fn todo_takeover_begins_work_but_never_invents_review_or_publication() {
    let mut db=fixture("todo");
    assert_eq!(run(&mut db,"local-client","recover",takeover("one"))["items"][0]["status"],"applied");
    assert_eq!(task(&mut db,"one")["status"],"in_progress");
}

#[test]
fn completion_requires_current_successful_verify_receipt() {
    let mut db=fixture("published");
    let done=|revision|Command::TransitionTask{task_id:"one".into(),expected_revision:revision,status:"done".into()};
    assert_eq!(run(&mut db,"old-agent","no-proof",done(7))["items"][0]["status"],"conflict");
    for (op,target,passed) in [("failed","verify",false),("wrong-target","test",true)] {
        run(&mut db,"build-runner",op,Command::RecordVerification{task_id:"one".into(),expected_revision:7,target:target.into(),passed});
        assert_eq!(run(&mut db,"old-agent",&format!("done-{op}"),done(7))["items"][0]["status"],"conflict");
    }
    run(&mut db,"build-runner","passed",Command::RecordVerification{task_id:"one".into(),expected_revision:7,target:"verify".into(),passed:true});
    run(&mut db,"old-agent","edit",Command::EditTask{task_id:"one".into(),expected_revision:7,title:Some("Changed".into()),priority:None});
    assert_eq!(run(&mut db,"old-agent","stale-proof",done(8))["items"][0]["status"],"conflict");
    run(&mut db,"build-runner","fresh",Command::RecordVerification{task_id:"one".into(),expected_revision:8,target:"verify".into(),passed:true});
    assert_eq!(run(&mut db,"old-agent","finished",done(8))["items"][0]["status"],"applied");
}
