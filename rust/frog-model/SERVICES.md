# Model service operations

CLI, TUI and MCP use HTTP by default. Selection order: explicit options,
environment, main-config `model_client`, sidecar config. Use `--legacy-storage`
(or explicit `--db`) for direct legacy access. HTTP failures never trigger
fallback. Default URL: `http://localhost:9091/`.

## Task editing

`frog task edit ID --title TEXT --priority p1 --why TEXT --what TEXT --roi-note TEXT`
and `--repo MODEL_REPOSITORY_ID` use revision-checked atomic edits. Omitted fields
are preserved; an empty metadata string clears the note. Workflow, assignee and
status-observation time are unchanged. Receipts/events are replay-safe. The token
currently authenticates as `local-client`; other actor overrides are refused.

## Providers

`frog provider pull --source jira --file normalized.json`, `outbox`, and configured
Asana/Linear/Jira `sync` use the API. Adapter configs need `repo_ref` naming a model
repository for new tasks. Original external IDs and why/what notes are retained.
Pulls update source-owned, unassigned tasks with revision checks; locally assigned
tasks conflict. Each item is a transaction, not an atomic batch. Outboxes paginate.
`idea`/`open` map to `todo`; manually `blocked` items are refused because blocking
must be represented by dependencies. Explicit adapter status maps can resolve this.

## Build verification

`frog task verify ID --target verify` runs targets configured on the server with
`FROG_MODEL_BUILD_CONFIG=/private/path/build-targets.json`:

```json
{"local":{"MODEL_REPOSITORY_ID":{"verify":{
  "argv":["/absolute/path/to/cargo","test","--features","sqlite,http"],
  "cwd":"/absolute/server/repository/rust/frog-model",
  "timeout_seconds":120
}}}}
```

Clients submit only task, target and operation ID, never commands or paths.
The runner removes the bearer token from child environment, suppresses output,
and records pass/fail against the revision read before execution. An edit during
the build causes receipt publication to conflict. Clients cannot forge receipts.
Durable reservations prevent concurrent/interrupted runs from being repeated;
completed results replay. Targets must be trusted bounded foreground commands:
timeout stops the direct child, not detached descendants. Set the client timeout
above the target timeout. Verification is separate from workflow transitions,
not yet an automatic finish gate or source-commit attestation.

## Exclusive leases

```sh
frog lock acquire --scope-key repo:build --lock-kind exclusive \
  --agent local-client --lease-seconds 60
frog lock renew LEASE_ID --lease-seconds 60
frog lock release LEASE_ID
frog lock list
```

Leases are workspace-scoped, exclusive, server-timed (1–3600 seconds), and fenced
by lease ID. Expired scopes can be reacquired; old owners/IDs cannot release a
replacement. They are advisory scope leases, not legacy file/working-tree lock
enforcement. File/PID/shared-kind/force/reason/ETA metadata and inactive history
remain unsupported with explicit errors. All tokens currently share `local-client`;
this is not multi-principal authorization.

MCP exposes task edit/verify, provider import/outbox and lock acquire/list/renew/
release through the same backend. Other unavailable legacy operations still fail
explicitly; this is not complete CLI/TUI/MCP parity.
