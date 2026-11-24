-- Native-host work parking is explicit; lease expiry is never a handoff.
CREATE TABLE IF NOT EXISTS frog_model_task_pauses (
 workspace_id TEXT NOT NULL,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
 actor TEXT NOT NULL,reason TEXT NOT NULL,receiver TEXT,
 PRIMARY KEY(workspace_id,task_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id)
);
CREATE TABLE IF NOT EXISTS frog_model_work_handoffs (
 workspace_id TEXT NOT NULL,operation_id TEXT NOT NULL,request_key TEXT NOT NULL,
 task_id TEXT NOT NULL,actor TEXT NOT NULL,kind TEXT NOT NULL,
 observed_revision INTEGER NOT NULL,result_revision INTEGER NOT NULL,
 lease_id TEXT NOT NULL,reason TEXT NOT NULL,receiver TEXT,
 created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(workspace_id,operation_id)
);
