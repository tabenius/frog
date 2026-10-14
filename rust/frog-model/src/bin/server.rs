use bytes::Bytes;
use http_body_util::{BodyExt, Full, Limited};
use hyper::{body::Incoming, service::service_fn, Request, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};
use std::{
    convert::Infallible,
    sync::{Arc, Mutex},
    time::Duration,
};

type State = Arc<(Mutex<Connection>, String, String, Option<String>)>;

fn verify_build(state: &State, workspace: &str, body: &Value) -> Result<Value, String> {
    let config_path = std::env::var("FROG_MODEL_BUILD_CONFIG")
        .map_err(|_| "server has no configured build targets")?;
    let config: Value = serde_json::from_str(
        &std::fs::read_to_string(config_path).map_err(|_| "build config unavailable")?,
    )
    .map_err(|_| "invalid build config")?;
    verify_build_configured(state, workspace, body, &config)
}

fn verify_build_configured(
    state: &State,
    workspace: &str,
    body: &Value,
    config: &Value,
) -> Result<Value, String> {
    if state.3.is_some() {
        return Err("build verification is unavailable in legacy mode".into());
    }
    if !body.as_object().is_some_and(|v| v.len() == 3) {
        return Err("expected only task_id, target and operation_id".into());
    }
    let task = body
        .get("task_id")
        .and_then(Value::as_str)
        .ok_or("task_id required")?;
    let target = body
        .get("target")
        .and_then(Value::as_str)
        .ok_or("target required")?;
    let operation = body["operation_id"]
        .as_str()
        .ok_or("operation_id required")?;
    frog_model::plan(frog_model::Request {
        workspace_id: workspace.into(),
        actor: "build-runner".into(),
        operation_id: operation.into(),
        command: frog_model::Command::RecordVerification {
            task_id: task.into(),
            expected_revision: 0,
            target: target.into(),
            passed: false,
        },
    })?;
    let row = {
        let mut conn = state.0.lock().map_err(|_| "storage lock failed")?;
        let mut query = conn.prepare(&format!("SELECT task_id,target,result FROM {}build_runs WHERE workspace_id=?1 AND operation_id=?2", state.1)).map_err(|_|"build lookup failed")?;
        let mut rows = query
            .query([workspace, operation])
            .map_err(|_| "build lookup failed")?;
        if let Some(row) = rows.next().map_err(|_| "build lookup failed")? {
            let saved_task: String = row.get(0).map_err(|_| "build lookup failed")?;
            let saved_target: String = row.get(1).map_err(|_| "build lookup failed")?;
            if saved_task != task || saved_target != target {
                return Err("build operation ID reused with a different request".into());
            }
            let result: Option<String> = row.get(2).map_err(|_| "build lookup failed")?;
            return match result {
                Some(result) => {
                    serde_json::from_str(&result).map_err(|_| "saved build result invalid".into())
                }
                None => Err("build is running or interrupted; refusing to rerun".into()),
            };
        }
        drop(rows);
        drop(query);
        let exists: bool = conn.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {}operations WHERE workspace_id=?1 AND operation_id=?2)",state.1),[workspace,operation],|r|r.get(0)).map_err(|_|"receipt lookup failed")?;
        if exists {
            return Err("build operation ID already used; refusing to rerun".into());
        }
        frog_model::sqlite::execute_with_prefix(
            &mut conn,
            frog_model::Request {
                workspace_id: workspace.into(),
                actor: "build-runner".into(),
                operation_id: "read".into(),
                command: frog_model::Command::DescribeTask {
                    task_id: task.into(),
                },
            },
            &state.1,
        )?
    };
    let task_row = row["items"]
        .as_array()
        .and_then(|r| r.first())
        .ok_or("task not found")?;
    let revision = task_row["revision"].as_u64().ok_or("invalid revision")? as u32;
    let repo = task_row["repository"]
        .as_str()
        .ok_or("missing repository")?;
    let specification = &config[workspace][repo][target];
    let argv = specification["argv"]
        .as_array()
        .ok_or("target is not configured on the server")?;
    let argv: Vec<&str> = argv
        .iter()
        .map(|v| v.as_str().ok_or("argv must be strings"))
        .collect::<Result<_, _>>()?;
    if argv.is_empty() {
        return Err("empty build argv".into());
    }
    let cwd = specification["cwd"]
        .as_str()
        .ok_or("server build cwd required")?;
    let timeout = specification["timeout_seconds"]
        .as_u64()
        .unwrap_or(60)
        .clamp(1, 300);
    {
        let conn = state.0.lock().map_err(|_| "storage lock failed")?;
        conn.execute(&format!("INSERT INTO {}build_runs(workspace_id,operation_id,task_id,target,revision) SELECT ?1,?2,?3,?4,?5 WHERE NOT EXISTS(SELECT 1 FROM {}operations WHERE workspace_id=?1 AND operation_id=?2)",state.1,state.1),rusqlite::params![workspace,operation,task,target,revision]).map_err(|_|"build operation already reserved")?
            .eq(&1).then_some(()).ok_or("build operation ID already used")?;
    }
    let mut child = std::process::Command::new(argv[0])
        .args(&argv[1..])
        .current_dir(cwd)
        .env_remove("FROG_MODEL_TOKEN")
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|_| "build launch failed")?;
    let start = std::time::Instant::now();
    let passed = loop {
        if let Some(status) = child.try_wait().map_err(|_| "build wait failed")? {
            break status.success();
        }
        if start.elapsed().as_secs() >= timeout {
            let _ = child.kill();
            let _ = child.wait();
            break false;
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let mut conn = state.0.lock().map_err(|_| "storage lock failed")?;
    let receipt = frog_model::sqlite::execute_with_prefix(
        &mut conn,
        frog_model::Request {
            workspace_id: workspace.into(),
            actor: "build-runner".into(),
            operation_id: operation.into(),
            command: frog_model::Command::RecordVerification {
                task_id: task.into(),
                expected_revision: revision,
                target: target.into(),
                passed,
            },
        },
        &state.1,
    )?;
    let result = json!({"schema":"frog.model-result.v1","kind":"build_verification","items":[{"task_id":task,"revision":revision,"target":target,"passed":passed,"receipt":receipt}]});
    conn.execute(
        &format!(
            "UPDATE {}build_runs SET result=?3 WHERE workspace_id=?1 AND operation_id=?2",
            state.1
        ),
        rusqlite::params![workspace, operation, result.to_string()],
    )
    .map_err(|_| "build result publication failed")?;
    Ok(result)
}
fn response(status: StatusCode, value: Value) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .header("Content-Type", "application/json")
        .body(Full::new(Bytes::from(value.to_string())))
        .unwrap()
}
async fn handle(req: Request<Incoming>, state: State) -> Result<Response<Full<Bytes>>, Infallible> {
    if req.uri().path() == "/health" {
        return Ok(response(
            StatusCode::OK,
            json!({"schema":"frog.model.v1","ok":true}),
        ));
    }
    if req
        .headers()
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        != Some(state.2.as_str())
    {
        return Ok(response(
            StatusCode::UNAUTHORIZED,
            json!({"error":"unauthorized"}),
        ));
    }
    let path: Vec<_> = req.uri().path().split('/').collect();
    if path.len() != 5 || path[1] != "v1" || path[2] != "workspaces" {
        return Ok(response(
            StatusCode::NOT_FOUND,
            json!({"error":"unknown route"}),
        ));
    }
    let workspace = path[3].to_string();
    let resource = path[4].to_string();
    let query: std::collections::HashMap<String, String> =
        form_urlencoded::parse(req.uri().query().unwrap_or("").as_bytes())
            .into_owned()
            .collect();
    let method = req.method().clone();
    let body = match Limited::new(req.into_body(), 16384).collect().await {
        Ok(body) => body.to_bytes(),
        Err(_) => {
            return Ok(response(
                StatusCode::PAYLOAD_TOO_LARGE,
                json!({"error":"body limit"}),
            ))
        }
    };
    if method == hyper::Method::POST && resource == "verify" {
        let value = match serde_json::from_slice::<Value>(&body) {
            Ok(value) => value,
            Err(_) => {
                return Ok(response(
                    StatusCode::BAD_REQUEST,
                    json!({"error":"invalid JSON"}),
                ))
            }
        };
        let result =
            tokio::task::spawn_blocking(move || verify_build(&state, &workspace, &value)).await;
        return Ok(match result {
            Ok(Ok(value)) => response(StatusCode::OK, value),
            Ok(Err(error)) => response(StatusCode::BAD_REQUEST, json!({"error":error})),
            Err(_) => response(
                StatusCode::SERVICE_UNAVAILABLE,
                json!({"error":"build runner failed"}),
            ),
        });
    }
    let input: Result<frog_model::Request, String> = if method == hyper::Method::POST
        && resource == "operations"
    {
        serde_json::from_slice::<Value>(&body)
            .map_err(|e| e.to_string())
            .and_then(|v| {
                if v["command"]["kind"] == "record_verification" {
                    return Err(
                        "verification receipts may only be written by the server runner".into(),
                    );
                }
                serde_json::from_value(json!({"workspace_id":workspace,"actor":"local-client",
                "operation_id":v["operation_id"],"command":v["command"]}))
                .map_err(|e| e.to_string())
            })
    } else if method == hyper::Method::GET {
        let limit = query.get("limit").map(|v| v.parse::<u32>()).transpose();
        let limit = match limit {
            Ok(value) => value.unwrap_or(200),
            Err(_) => {
                return Ok(response(
                    StatusCode::BAD_REQUEST,
                    json!({"error":"invalid limit"}),
                ))
            }
        };
        let command = match resource.as_str() {
            "board" => Some(json!({"kind":"board","repo_id":query.get("repo"),"limit":limit,"after":query.get("after")})),
            "repositories" => Some(json!({"kind":"repositories","limit":limit})),
            "events" => Some(json!({"kind":"events","after":query.get("after").map(String::as_str).unwrap_or("0"),"limit":limit})),
            "tasks" => query.get("task").map(|task| json!({"kind":"describe_task","task_id":task})),
            "task-details" => query.get("task").map(|task|json!({"kind":"task_details","task_id":task})),
            "provider-outbox" => query.get("source").map(|source|json!({"kind":"provider_outbox","source":source,"limit":limit,"after":query.get("after")})),
            "verifications" => query.get("task").map(|task|json!({"kind":"verifications","task_id":task})),
            "locks" => Some(json!({"kind":"locks","limit":limit,"after":query.get("after")})),
            _ => None,
        };
        command.ok_or_else(|| "unsupported resource".to_string()).and_then(|command|
            serde_json::from_value(json!({"workspace_id":workspace,"actor":"local-client","operation_id":"read","command":command})).map_err(|e| e.to_string()))
    } else {
        Err("unsupported method".into())
    };
    let input = match input {
        Ok(input) => input,
        Err(error) => return Ok(response(StatusCode::BAD_REQUEST, json!({"error":error}))),
    };
    let result = tokio::task::spawn_blocking(move || {
        let mut conn = state.0.lock().map_err(|_| "storage lock failed".to_string())?;
        if state.3.is_none() && method == hyper::Method::POST {
            let reserved: bool = conn.query_row(&format!("SELECT EXISTS(SELECT 1 FROM {}build_runs WHERE workspace_id=?1 AND operation_id=?2)",state.1),rusqlite::params![input.workspace_id,input.operation_id],|r|r.get(0)).map_err(|_|"build reservation lookup failed")?;
            if reserved {return Err("operation ID is reserved by a build".into());}
        }
        match &state.3 {
            Some(workspace) => frog_model::legacy::execute(&mut conn, input, workspace, &state.1),
            None => frog_model::sqlite::execute_with_prefix(&mut conn, input, &state.1),
        }
    }).await;
    Ok(match result {
        Ok(Ok(value)) => {
            let conflict = value["items"].as_array().is_some_and(|items| {
                items.iter().any(|v| {
                    matches!(
                        v["status"].as_str(),
                        Some("conflict" | "idempotency_conflict")
                    )
                })
            });
            response(
                if conflict {
                    StatusCode::CONFLICT
                } else {
                    StatusCode::OK
                },
                value,
            )
        }
        _ => response(
            StatusCode::SERVICE_UNAVAILABLE,
            json!({"error":"storage operation failed"}),
        ),
    })
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let db = std::env::var("FROG_MODEL_DB_PATH")?;
    let prefix = std::env::var("FROG_MODEL_TABLE_PREFIX").unwrap_or("frog_model_".into());
    if prefix.is_empty()
        || !prefix
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return Err("invalid table prefix".into());
    }
    let token = std::env::var("FROG_MODEL_TOKEN")?;
    if token.len() < 32 {
        return Err("token must contain at least 32 characters".into());
    }
    let mut conn = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_WRITE)?;
    conn.busy_timeout(Duration::from_secs(5))?;
    conn.pragma_update(None, "foreign_keys", "ON")?;
    // Verify the model schema before touching persistent storage.
    if std::env::var("FROG_MODEL_STORAGE").as_deref() != Ok("legacy") {
        conn.prepare(&format!("SELECT version FROM {prefix}schema"))?;
    }
    // Model extension schema is additive and transactional.
    if std::env::var("FROG_MODEL_STORAGE").as_deref() != Ok("legacy") {
        conn.execute_batch(&format!(
            "BEGIN IMMEDIATE; {} COMMIT;",
            frog_model::SERVICE_MIGRATION.replace("frog_model_", &prefix)
        ))?;
    }
    let legacy = if std::env::var("FROG_MODEL_STORAGE").as_deref() == Ok("legacy") {
        let workspace = std::env::var("FROG_MODEL_WORKSPACE")?;
        frog_model::legacy::prepare(&conn, &workspace, &prefix)?;
        if std::env::var("FROG_MODEL_INIT_RECEIPTS").as_deref() == Ok("1") {
            frog_model::legacy::initialise_receipts(&mut conn, &prefix)?;
        }
        Some(workspace)
    } else {
        // Never initialise an empty parallel store in a legacy workspace database.
        conn.prepare(&format!("SELECT version FROM {prefix}schema"))?;
        None
    };
    let state = Arc::new((Mutex::new(conn), prefix, format!("Bearer {token}"), legacy));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:9091").await?;
    eprintln!("Frog model HTTP listening on 127.0.0.1:9091");
    loop {
        let (stream, _) = listener.accept().await?;
        let state = state.clone();
        tokio::spawn(async move {
            if let Err(error) = hyper::server::conn::http1::Builder::new()
                .serve_connection(
                    TokioIo::new(stream),
                    service_fn(move |req| handle(req, state.clone())),
                )
                .await
            {
                eprintln!("HTTP connection failed: {error}");
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> State {
        let mut conn = Connection::open_in_memory().unwrap();
        conn.execute_batch("PRAGMA foreign_keys=ON").unwrap();
        conn.execute_batch(frog_model::MIGRATION).unwrap();
        conn.execute_batch(frog_model::SERVICE_MIGRATION).unwrap();
        conn.execute(
            "INSERT INTO frog_model_workspaces VALUES('alpha','Alpha')",
            [],
        )
        .unwrap();
        for (operation, command) in [
            (
                "repo",
                frog_model::Command::RegisterRepo {
                    repo_id: "frog".into(),
                    title: "Frog".into(),
                },
            ),
            (
                "task",
                frog_model::Command::CreateTask {
                    repo_id: "frog".into(),
                    task_id: "one".into(),
                    title: "One".into(),
                    priority: 0,
                },
            ),
        ] {
            frog_model::sqlite::execute(
                &mut conn,
                frog_model::Request {
                    workspace_id: "alpha".into(),
                    actor: "owner".into(),
                    operation_id: operation.into(),
                    command,
                },
            )
            .unwrap();
        }
        Arc::new((
            Mutex::new(conn),
            "frog_model_".into(),
            "unused".into(),
            None,
        ))
    }

    #[test]
    fn server_runs_only_allowlisted_targets_and_replays_results() {
        let state = fixture();
        let config = json!({"alpha":{"frog":{
            "test":{"argv":["/bin/true"],"cwd":"/"},
            "fail":{"argv":["/bin/false"],"cwd":"/"}
        }}});
        let body = json!({"task_id":"one","target":"test","operation_id":"verify"});
        let first = verify_build_configured(&state, "alpha", &body, &config).unwrap();
        assert_eq!(first["items"][0]["passed"], true);
        assert_eq!(
            first["items"][0]["receipt"]["items"][0]["status"],
            "applied"
        );
        assert_eq!(
            verify_build_configured(&state, "alpha", &body, &config).unwrap(),
            first
        );
        assert!(verify_build_configured(
            &state,
            "alpha",
            &json!({"task_id":"one","target":"fail","operation_id":"verify"}),
            &config
        )
        .is_err());
        assert!(verify_build_configured(
            &state,
            "alpha",
            &json!({"task_id":"one","target":"arbitrary","operation_id":"unknown"}),
            &config
        )
        .is_err());
        assert!(verify_build_configured(
            &state,
            "alpha",
            &json!({"task_id":"one","target":"test","operation_id":"injected","argv":["bad"]}),
            &config
        )
        .is_err());
        let failed = verify_build_configured(
            &state,
            "alpha",
            &json!({"task_id":"one","target":"fail","operation_id":"failed"}),
            &config,
        )
        .unwrap();
        assert_eq!(failed["items"][0]["passed"], false);
        assert!(verify_build_configured(&state, "beta", &body, &config).is_err());
    }

    #[test]
    fn reserved_build_is_never_implicitly_rerun() {
        let state = fixture();
        state.0.lock().unwrap().execute("INSERT INTO frog_model_build_runs(workspace_id,operation_id,task_id,target,revision) VALUES('alpha','reserved','one','test',0)",[]).unwrap();
        let config = json!({"alpha":{"frog":{"test":{"argv":["/bin/true"],"cwd":"/"}}}});
        let error = verify_build_configured(
            &state,
            "alpha",
            &json!({"task_id":"one","target":"test","operation_id":"reserved"}),
            &config,
        )
        .unwrap_err();
        assert!(error.contains("refusing to rerun"));
    }

    #[test]
    fn edits_during_build_conflict_and_concurrent_replays_do_not_run_again() {
        let state = fixture();
        let config =
            json!({"alpha":{"frog":{"test":{"argv":["/bin/sh","-c","sleep 0.2"],"cwd":"/"}}}});
        let body = json!({"task_id":"one","target":"test","operation_id":"slow-build"});
        let worker_state = state.clone();
        let worker_config = config.clone();
        let worker_body = body.clone();
        let worker = std::thread::spawn(move || {
            verify_build_configured(&worker_state, "alpha", &worker_body, &worker_config)
        });
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            let count: i64 = state
                .0
                .lock()
                .unwrap()
                .query_row("SELECT count(*) FROM frog_model_build_runs", [], |r| {
                    r.get(0)
                })
                .unwrap();
            if count == 1 {
                break;
            }
            assert!(
                std::time::Instant::now() < deadline,
                "build was not reserved"
            );
            std::thread::yield_now();
        }
        assert!(verify_build_configured(&state, "alpha", &body, &config).is_err());
        {
            let mut conn = state.0.lock().unwrap();
            frog_model::sqlite::execute(
                &mut conn,
                frog_model::Request {
                    workspace_id: "alpha".into(),
                    actor: "owner".into(),
                    operation_id: "edit-during-build".into(),
                    command: frog_model::Command::EditTask {
                        task_id: "one".into(),
                        expected_revision: 0,
                        title: Some("Changed".into()),
                        priority: None,
                    },
                },
            )
            .unwrap();
        }
        let result = worker.join().unwrap().unwrap();
        assert_eq!(result["items"][0]["passed"], true);
        assert_eq!(
            result["items"][0]["receipt"]["items"][0]["status"],
            "conflict"
        );
        assert_eq!(
            verify_build_configured(&state, "alpha", &body, &config).unwrap(),
            result
        );
        let count: i64 = state
            .0
            .lock()
            .unwrap()
            .query_row("SELECT count(*) FROM frog_model_verifications", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(count, 0);
    }
}
