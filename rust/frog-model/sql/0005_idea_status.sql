-- Native host runs this in a transaction with foreign_keys disabled, verifies
-- all foreign keys before commit, then restores enforcement. Never apply to
-- legacy AGENTS.db or run inside a host's existing transaction.
CREATE TABLE frog_model_tasks_idea (
  workspace_id TEXT NOT NULL, task_id TEXT NOT NULL, repo_id TEXT NOT NULL,
  title TEXT NOT NULL, priority INTEGER NOT NULL CHECK(priority BETWEEN 0 AND 3),
  status TEXT NOT NULL CHECK(status IN ('idea','todo','in_progress','review','published','done','cancelled')),
  assignee TEXT, revision INTEGER NOT NULL CHECK(revision BETWEEN 0 AND 4294967295),
  last_operation_id TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  status_observed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY(workspace_id,task_id),
  FOREIGN KEY(workspace_id,repo_id) REFERENCES frog_model_repos(workspace_id,repo_id),
  FOREIGN KEY(workspace_id,last_operation_id) REFERENCES frog_model_operations(workspace_id,operation_id)
);
INSERT INTO frog_model_tasks_idea SELECT * FROM frog_model_tasks;
DROP TABLE frog_model_tasks;
ALTER TABLE frog_model_tasks_idea RENAME TO frog_model_tasks;
CREATE INDEX frog_model_tasks_repo ON frog_model_tasks(workspace_id,repo_id,priority,task_id);
DROP TABLE frog_model_schema;
CREATE TABLE frog_model_schema(version INTEGER PRIMARY KEY CHECK(version=2));
INSERT INTO frog_model_schema VALUES(2);
