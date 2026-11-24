# Explicit native work parking

`task pause TASK --lease-id EXACT --reason TEXT` records a deliberate pause and
releases that exact live repository lease atomically. `task handoff` also requires
`--receiver NAME`: this is an intended receiver, not reassignment or impersonation.
Assignee and workflow status remain unchanged; revision advances and an audit
record binds the action to the observed revision, reason, receiver and lease.

`task resume TASK --reason TEXT` consumes the current pause and acquires a new
one-hour fenced repository lease in the same transaction. The response supplies
the new lease ID. Resume refuses stale revisions, competing repository leases,
reused handoff lease identities and other active unparked assignments. A reviewer
must still use the ordinary authorized takeover protocol to change ownership.

All three commands use the authenticated server actor. New native servers enforce
claim exclusivity in an immediate SQLite transaction: another in-progress,
review or published assignment blocks unless its current revision has an explicit
pause and no live repository lease. Lease expiry alone is **not** permission to
start other work. Competing claims cannot both pass. Existing source leases are
not borrowed from another task or silently discarded.

Native service initialization adds the pause and handoff-audit tables idempotently.
No live migration/restart is performed by a source update. Existing lease-released
review work has no pause record: deliberately reacquire its free repository lease
and pause it before new claims. If a competing lease exists, stop.

These are native-host endpoints, not a WIT world or D1/legacy lifecycle change.
The CLI discovers native parking support; older hosts retain their existing
client claim preflight, and pause/resume/handoff fail rather than using a legacy
database fallback. Audit events use `task.pause`, `task.handoff`, `task.resume`;
do not put credentials or private prompts in reasons or receiver names. A replay
receipt describes the original result, not proof the returned lease is still live.
