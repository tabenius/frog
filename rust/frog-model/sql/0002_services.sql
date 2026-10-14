CREATE TABLE IF NOT EXISTS frog_model_provider_items (
 workspace_id TEXT NOT NULL,source TEXT NOT NULL,external_id TEXT NOT NULL,task_id TEXT NOT NULL,
 PRIMARY KEY(workspace_id,source,external_id),UNIQUE(workspace_id,task_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id) DEFERRABLE INITIALLY DEFERRED);
CREATE TABLE IF NOT EXISTS frog_model_task_details (
 workspace_id TEXT NOT NULL,task_id TEXT NOT NULL,why TEXT,what_text TEXT,roi_note TEXT,
 PRIMARY KEY(workspace_id,task_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id));
CREATE TABLE IF NOT EXISTS frog_model_verifications (
 workspace_id TEXT NOT NULL,task_id TEXT NOT NULL,revision INTEGER NOT NULL,
 passed INTEGER NOT NULL CHECK(passed IN (0,1)),target TEXT NOT NULL,
 checked_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(workspace_id,task_id,revision,target),
 FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id));
CREATE TABLE IF NOT EXISTS frog_model_locks (
 workspace_id TEXT NOT NULL,scope TEXT NOT NULL,owner TEXT NOT NULL,
 lease_id TEXT NOT NULL,expires_at INTEGER NOT NULL,
 PRIMARY KEY(workspace_id,scope),
 FOREIGN KEY(workspace_id) REFERENCES frog_model_workspaces(workspace_id));
-- Reserve before executing a build. An interrupted run is never rerun
-- implicitly; completed results can be replayed without launching commands.
CREATE TABLE IF NOT EXISTS frog_model_build_runs (
 workspace_id TEXT NOT NULL,operation_id TEXT NOT NULL,task_id TEXT NOT NULL,
 target TEXT NOT NULL,revision INTEGER NOT NULL,result TEXT,
 PRIMARY KEY(workspace_id,operation_id),
 FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id));
