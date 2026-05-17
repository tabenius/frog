CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repos (
    repo_path TEXT PRIMARY KEY,
    name TEXT UNIQUE,
    kind TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    third_party INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    file_path TEXT PRIMARY KEY,
    repo_path TEXT,
    file_type TEXT,
    source_of_truth TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (repo_path) REFERENCES repos(repo_path) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_files_repo_path ON files(repo_path);
CREATE INDEX IF NOT EXISTS idx_files_type ON files(file_type);

CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    kind TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL,
    repo_path TEXT,
    lock_kind TEXT NOT NULL,
    file_paths_json TEXT NOT NULL DEFAULT '[]',
    agent_name TEXT NOT NULL,
    pid INTEGER,
    host TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    lease_seconds INTEGER NOT NULL DEFAULT 1800,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    eta_finish_at TEXT,
    released_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_locks_status ON locks(status);
CREATE INDEX IF NOT EXISTS idx_locks_repo_status ON locks(repo_path, status);
CREATE INDEX IF NOT EXISTS idx_locks_scope_status ON locks(scope_key, status);

CREATE TABLE IF NOT EXISTS tasks (
    slug TEXT PRIMARY KEY,
    repo_path TEXT,
    title TEXT NOT NULL,
    why TEXT,
    what_text TEXT,
    roi_note TEXT,
    priority TEXT NOT NULL DEFAULT 'p3',
    workflow_status TEXT NOT NULL DEFAULT 'idea',
    git_status TEXT NOT NULL DEFAULT 'not_started',
    assigned_agent TEXT,
    delegation_current TEXT,
    delegation_other TEXT,
    parent_task_slug TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status_confidence_at TEXT NOT NULL,
    FOREIGN KEY (repo_path) REFERENCES repos(repo_path) ON DELETE SET NULL,
    FOREIGN KEY (parent_task_slug) REFERENCES tasks(slug) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_repo_path ON tasks(repo_path);
CREATE INDEX IF NOT EXISTS idx_tasks_workflow_status ON tasks(workflow_status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_slug TEXT NOT NULL,
    depends_on_slug TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'depends_on',
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_slug, depends_on_slug, relation),
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_slug) REFERENCES tasks(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_conflicts (
    task_slug TEXT NOT NULL,
    conflicts_with_slug TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_slug, conflicts_with_slug),
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE CASCADE,
    FOREIGN KEY (conflicts_with_slug) REFERENCES tasks(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_slug TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_slug, tag),
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_slug TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    notes TEXT,
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE CASCADE,
    FOREIGN KEY (agent_name) REFERENCES agents(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_slug TEXT NOT NULL,
    workflow_status TEXT,
    git_status TEXT,
    note TEXT,
    changed_at TEXT NOT NULL,
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_files (
    task_slug TEXT NOT NULL,
    file_path TEXT NOT NULL,
    role TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_slug, file_path),
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE CASCADE,
    FOREIGN KEY (file_path) REFERENCES files(file_path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    repo_path TEXT,
    task_slug TEXT,
    actor TEXT,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (repo_path) REFERENCES repos(repo_path) ON DELETE SET NULL,
    FOREIGN KEY (task_slug) REFERENCES tasks(slug) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_log_created_at ON event_log(created_at);
CREATE INDEX IF NOT EXISTS idx_event_log_repo_path ON event_log(repo_path);
CREATE INDEX IF NOT EXISTS idx_event_log_task_slug ON event_log(task_slug);
