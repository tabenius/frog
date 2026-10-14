-- Preserve existing destinations' envelope; automatic delivery stays opt-in.
ALTER TABLE event_hooks ADD COLUMN signing_key_file TEXT;
ALTER TABLE event_hooks ADD COLUMN payload_schema TEXT NOT NULL DEFAULT 'frog.legacy-hook.v0';
