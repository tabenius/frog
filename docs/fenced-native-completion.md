# Fenced native completion

The native HTTP service completes work through `POST /v1/workspaces/{id}/finish`.
Ordinary `transition_task` requests cannot mark a task done. The caller supplies
`task_id`, `expected_revision`, `operation_id`, `lease_id` and `build_operation`.
The authenticated actor is always supplied by the server, never by the caller.

The server runner records a source identity for the `verify` target: the Git
HEAD plus the complete configured target specification. The source worktree
must be clean (including untracked files), before and after verification and
when finish is requested. Changing the command, working directory or timeout
invalidates the old identity. Builds which modify source cannot pass verification.
Generated files must be ignored deliberately, not hidden from the checker.

Completion checks the matching successful native build result, task revision,
assignee, published state, verification age (at most five minutes) and exact live
repository lease in one immediate SQLite write transaction. It transitions the
task and deletes that exact lease in the same transaction. A failed precondition
does not release the lease or complete the task. No client-side transition or
fallback is allowed. An older server lacking the endpoint/evidence fails closed.

These checks observe source identity; they do not seal the filesystem against
an unrelated writer after observation. Use an isolated clean worktree and the
repository lease. A future hermetic build snapshot would strengthen that boundary.
Build dependencies, toolchain executables and ignored files are not content-pinned
by this identity; pin those in the project's lockfiles/build environment.

This endpoint is native-host functionality, not a new WIT component operation or
a change to D1/legacy completion policy. No deployed service is automatically
upgraded. Verification receipts created before this feature lack the identity and
cannot finish through it. Replaying a finish after success fails the live-lease
precondition; inspect task state rather than treating a retry error as proof of
failure or attempting another completion.
