CREATE TABLE IF NOT EXISTS event_hooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'webhook',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_event_id INTEGER NOT NULL DEFAULT 0,
    last_status INTEGER,
    last_error TEXT,
    last_sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_hooks_enabled ON event_hooks(enabled);
