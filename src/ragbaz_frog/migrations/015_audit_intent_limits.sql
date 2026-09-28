-- Bounded admission accounting for audit-before-action intents. One minute of
-- actor counters is kept at a time; actor cardinality is bounded by the global
-- acceptance cap. A limited burst is represented by one compact event per
-- actor/window (or one global event), never one event per rejected request.
CREATE TABLE audit_intent_windows (
    window_minute INTEGER NOT NULL,
    actor TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted >= 0),
    PRIMARY KEY (window_minute, actor)
);
