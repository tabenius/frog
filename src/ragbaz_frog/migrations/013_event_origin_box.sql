-- federation-causality: stamp every event with the box that produced it.
-- Existing summaries stay human-readable; these columns let log why/blame
-- answer cross-box questions without parsing freeform text.
ALTER TABLE event_log ADD COLUMN origin_box_id TEXT;
ALTER TABLE event_log ADD COLUMN origin_host TEXT;
CREATE INDEX IF NOT EXISTS idx_event_log_origin_box ON event_log(origin_box_id);
