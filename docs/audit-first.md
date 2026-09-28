# Audit-first repository operations

Frog records repository initialization, registration, and agent-instruction
write intents before touching the filesystem or registering state. Each accepted
intent is committed to both `event_log` and a per-database `*.frog-intents.jsonl`
Nostoi chain before control returns to the operation. The chain uses the
suite's stdlib reference at `nostoi/contrib/python/nostoi.py`; when installed
outside the workspace, configure `FROG_NOSTOI_PYTHON`. `FROG_NOSTOI_LEDGER`
overrides the default chain path.

The SQLite event row leaves foreign-key resource columns empty before resource
creation and stores the planned identity in a bounded payload. `intent_id`
joins the later success event to the durable request. A chain entry without a
success event means the attempt may have started and must be reconciled; it does
not claim the operation completed.

Every intent payload is capped at 4 KiB. Admission is capped at 600 intents per
workspace and 60 per actor per UTC minute. When a limit is reached, Frog writes
one compact rate-limit record for that scope in the current minute; subsequent
denied requests in that already-recorded scope do read-only checks and are
refused without more audit writes. The service also relies on upstream request
limits where it is exposed remotely. If either durable store is unavailable or
the Nostoi chain is broken, the repository side effect is refused.

The shared suite contract and migration sequence are described in Nostoi's
[`AUDIT-FIRST.md`](../../nostoi/docs/AUDIT-FIRST.md). Other suite services still
need to apply that contract to their own side effects; this document does not
assert that migration is complete.
