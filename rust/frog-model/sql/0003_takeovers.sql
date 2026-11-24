-- Additive service schema. Core model schema and the existing WIT ABI stay v1.
CREATE TABLE IF NOT EXISTS frog_model_task_takeovers (
 workspace_id TEXT NOT NULL,operation_id TEXT NOT NULL,task_id TEXT NOT NULL,
 previous_assignee TEXT,assignee TEXT NOT NULL,actor TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 500),
 revision INTEGER NOT NULL,
 changed_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 PRIMARY KEY(workspace_id,operation_id),UNIQUE(workspace_id,task_id,revision),
 FOREIGN KEY(workspace_id,task_id) REFERENCES frog_model_tasks(workspace_id,task_id),
 FOREIGN KEY(workspace_id,operation_id) REFERENCES frog_model_operations(workspace_id,operation_id));
