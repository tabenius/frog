#![cfg(feature = "sqlite")]
use frog_model::{Command, Request};
use rusqlite::{params, Connection};
use serde_json::Value;

#[test]
fn shared_status_fixture_matches_actual_model_board_lanes() {
    let fixture: Value = serde_json::from_str(include_str!("../../../contracts/task-observations.v1.json")).unwrap();
    let mut db = Connection::open_in_memory().unwrap();
    db.execute_batch("PRAGMA foreign_keys=ON").unwrap();
    db.execute_batch(frog_model::MIGRATION).unwrap();
    db.execute_batch(frog_model::SERVICE_MIGRATION).unwrap();
    frog_model::sqlite::upgrade_idea_schema(&mut db, "frog_model_").unwrap();
    db.execute("INSERT INTO frog_model_workspaces VALUES('fixtures','Fixtures')", []).unwrap();
    db.execute("INSERT INTO frog_model_operations(workspace_id,operation_id,request_key,status) VALUES('fixtures','seed','seed','applied')", []).unwrap();
    db.execute("INSERT INTO frog_model_repos VALUES('fixtures','repo','Repo','seed')", []).unwrap();
    for case in fixture["statuses"].as_array().unwrap() {
        let status = case["status"].as_str().unwrap();
        db.execute("INSERT INTO frog_model_tasks(workspace_id,task_id,repo_id,title,priority,status,revision,last_operation_id) VALUES('fixtures',?1,'repo',?1,0,?1,0,'seed')", params![status]).unwrap();
    }
    let result = frog_model::sqlite::execute(&mut db, Request {workspace_id:"fixtures".into(),actor:"reader".into(),operation_id:"read".into(),command:Command::Board{repo_id:None,limit:200,after:None}}).unwrap();
    for case in fixture["statuses"].as_array().unwrap() {
        let row = result["items"].as_array().unwrap().iter().find(|row| row["id"] == case["status"]).unwrap();
        assert_eq!(row["lane"], case["frog_lane"]);
        assert_eq!(row["priority"], 0);
        assert!(row["assignee"].is_null());
    }
}
