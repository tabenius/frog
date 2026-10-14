-- Separate opt-in model database. Never apply to a legacy AGENTS.db implicitly.
CREATE TABLE frog_model_schema(version INTEGER PRIMARY KEY CHECK(version=1));
INSERT INTO frog_model_schema VALUES(1);
CREATE TABLE frog_model_workspaces(workspace_id TEXT PRIMARY KEY, title TEXT NOT NULL);
CREATE TABLE frog_model_operations(
  workspace_id TEXT NOT NULL REFERENCES frog_model_workspaces(workspace_id),
  operation_id TEXT NOT NULL, request_key TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','applied','conflict')),
  PRIMARY KEY(workspace_id,operation_id)
);
CREATE TABLE frog_model_repos(
  workspace_id TEXT NOT NULL REFERENCES frog_model_workspaces(workspace_id),
  repo_id TEXT NOT NULL, title TEXT NOT NULL, last_operation_id TEXT NOT NULL,
  PRIMARY KEY(workspace_id,repo_id),
  FOREIGN KEY(workspace_id,last_operation_id) REFERENCES frog_model_operations(workspace_id,operation_id)
);
CREATE TABLE frog_model_tasks(
  workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, repo_id TEXT NOT NULL,
  title TEXT NOT NULL, priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 3),
  status TEXT NOT NULL CHECK(status IN ('todo','in_progress','review','published','done','cancelled')),
  assignee TEXT, revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 4294967295),
  last_operation_id TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY(workspace_id,task_id),
  FOREIGN KEY(workspace_id,repo_id) REFERENCES frog_model_repos(workspace_id,repo_id),
  FOREIGN KEY(workspace_id,last_operation_id) REFERENCES frog_model_operations(workspace_id,operation_id)
);
CREATE TABLE frog_model_dependencies(
  workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, depends_on TEXT NOT NULL,
  PRIMARY KEY(workspace_id,task_id,depends_on),
  CHECK(task_id!=depends_on),
  FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id),
  FOREIGN KEY(workspace_id,depends_on) REFERENCES frog_model_tasks(workspace_id,task_id)
);
CREATE TABLE frog_model_events(
  workspace_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence>0), operation_id TEXT NOT NULL,
  entity_kind TEXT NOT NULL CHECK(entity_kind IN ('repository','task')),
  entity_id TEXT NOT NULL, kind TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE(workspace_id,operation_id),
  PRIMARY KEY(workspace_id,sequence),
  FOREIGN KEY(workspace_id,operation_id) REFERENCES frog_model_operations(workspace_id,operation_id)
);
CREATE INDEX frog_model_tasks_repo ON frog_model_tasks(workspace_id,repo_id,priority,task_id);
