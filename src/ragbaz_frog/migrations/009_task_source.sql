-- task-provider-adapter: tasks can originate from / sync to an external
-- system (GitHub, Asana, ...). (source, external_id) is the idempotency
-- key for inbound sync; status round-trips via the outbox.
ALTER TABLE tasks ADD COLUMN source TEXT;
ALTER TABLE tasks ADD COLUMN external_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_source_extid
ON tasks(source, external_id) WHERE source IS NOT NULL;
