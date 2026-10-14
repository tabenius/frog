#![cfg(feature = "sqlite")]
use frog_model::{legacy, Request};
use rusqlite::Connection;
use serde_json::{json, Value};

fn fixture() -> Connection {
    let mut conn = Connection::open_in_memory().unwrap();
    conn.execute_batch("PRAGMA foreign_keys=ON;
        CREATE TABLE repos(repo_path TEXT PRIMARY KEY,name TEXT,repo_key TEXT);
        CREATE TABLE tasks(slug TEXT PRIMARY KEY,repo_path TEXT REFERENCES repos(repo_path),title TEXT,
          priority TEXT,workflow_status TEXT,assigned_agent TEXT,git_status TEXT,why TEXT,
          created_at TEXT,updated_at TEXT,status_confidence_at TEXT);
        CREATE TABLE task_dependencies(task_slug TEXT REFERENCES tasks(slug),depends_on_slug TEXT REFERENCES tasks(slug),relation TEXT,created_at TEXT,PRIMARY KEY(task_slug,depends_on_slug,relation));
        CREATE TABLE event_log(id INTEGER PRIMARY KEY,created_at TEXT,kind TEXT,task_slug TEXT,actor TEXT,summary TEXT,payload_json TEXT);
        INSERT INTO repos VALUES('/private/path/frog','frog','repo:frog');
        INSERT INTO tasks VALUES('old','/private/path/frog','Existing','p0','idea',NULL,'not_started','preserve me','2026-10-01','2026-10-02','2026-10-01');").unwrap();
    legacy::prepare(&conn,"local","frog_model_").unwrap();
    legacy::initialise_receipts(&mut conn,"frog_model_").unwrap();
    conn
}
fn request(op: &str, command: Value) -> Request {
    serde_json::from_value(json!({"workspace_id":"local","actor":"test","operation_id":op,"command":command})).unwrap()
}
fn run(conn: &mut Connection, op: &str, command: Value) -> Value {
    legacy::execute(conn,request(op,command),"local","frog_model_").unwrap()
}
#[test]
fn live_reads_preserve_identity_status_and_private_fields() {
    let mut conn = fixture();
    let result = run(&mut conn,"read",json!({"kind":"board","repo_id":null,"limit":100,"after":null}));
    assert_eq!(result["items"][0]["id"],"old");
    assert_eq!(result["items"][0]["repository"],"repo:frog");
    assert_eq!(result["items"][0]["status"],"idea");
    assert!(!result.to_string().contains("/private/path"));
    conn.execute("UPDATE tasks SET title='Changed by legacy CLI' WHERE slug='old'",[]).unwrap();
    let result = run(&mut conn,"read2",json!({"kind":"describe_task","task_id":"old"}));
    assert_eq!(result["items"][0]["title"],"Changed by legacy CLI");
}
#[test]
fn writes_are_idempotent_preserve_legacy_fields_and_rollback_failures() {
    let mut conn = fixture();
    let create = json!({"kind":"create_task","repo_id":"repo:frog","task_id":"new","title":"New","priority":2});
    assert_eq!(run(&mut conn,"create",create.clone()),run(&mut conn,"create",create));
    let clash = run(&mut conn,"create",json!({"kind":"create_task","repo_id":"repo:frog","task_id":"other","title":"Other","priority":2}));
    assert_eq!(clash["items"][0]["status"],"idempotency_conflict");
    run(&mut conn,"depend",json!({"kind":"add_dependency","task_id":"new","depends_on":"old"}));
    assert!(legacy::execute(&mut conn,request("cycle",json!({"kind":"add_dependency","task_id":"old","depends_on":"new"})),"local","frog_model_").is_err());
    run(&mut conn,"remove",json!({"kind":"remove_dependency","task_id":"new","depends_on":"old"}));
    let why: String = conn.query_row("SELECT why FROM tasks WHERE slug='old'",[],|r|r.get(0)).unwrap();
    assert_eq!(why,"preserve me");
    assert!(legacy::execute(&mut conn,request("bad",json!({"kind":"add_dependency","task_id":"new","depends_on":"missing"})),"local","frog_model_").is_err());
    let count: i64 = conn.query_row("SELECT count(*) FROM frog_model_legacy_receipts WHERE operation_id IN ('bad','cycle')",[],|r|r.get(0)).unwrap();
    assert_eq!(count,0);
}
#[test]
fn tenancy_and_unsafe_mutations_are_refused() {
    let mut conn = fixture();
    let mut req = request("read",json!({"kind":"repositories","limit":10}));
    req.workspace_id="other".into();
    assert!(legacy::execute(&mut conn,req,"local","frog_model_").is_err());
    assert!(legacy::execute(&mut conn,request("claim",json!({"kind":"claim_task","task_id":"old","expected_revision":0})),"local","frog_model_").is_err());
}
