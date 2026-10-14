# Committed-event hooks

Hooks do not require `frog web serve`. The existing SQLite event log, hook
registry and per-destination cursor remain the authority; delivery adapters are
isolated in `ragbaz_frog.hooks`, suitable for a future Rust I/O implementation.

```sh
frog hook add https://receiver.example/frog --signing-key-file ~/.config/frog/hook.key
frog hook add-shell -- /absolute/path/to/receiver --mode ingest
frog hook dispatch --allow-shell-hooks
frog task create --slug next --title 'Next work' --hook 1
frog task create --slug other --title 'Other work' --webhook-url https://receiver.example/one-shot
frog task create --slug quiet --title 'Quiet work' --no-hooks
```

Use ordinary Frog configuration for automatic CLI delivery:

```yaml
hooks:
  auto_dispatch: true
  ids: [1, 2]       # omit to select all enabled registered hooks
  allow_shell: false
```

Defaults are off. `--hook ID` selects registered destinations; repeat it for
several. `--webhook-url URL` selects one-shot destinations rather than configured
defaults; combining both explicitly delivers to both. One-shot signing uses
`--hook-signing-key-file FILE`. `--no-hooks` suppresses automatic/one-shot delivery.
Reads do not trigger automatic delivery merely because command logging wrote a
row. Explicit `frog hook dispatch` remains an independent operation.

New hooks receive `frog.hook-events.v1`: minimal event kind, stable box/row event
ID, timestamp, task ID and repository basename. No event prose, argv, absolute
paths or arbitrary payloads. Names/IDs can still be private. Existing hooks keep
their historical envelope until re-registered; upgrading explicitly opts them
into v1. Migration016 adds metadata, never secrets.

Webhook adapters require HTTPS (plain HTTP only to loopback), reject URL
credentials/query/fragment and never follow redirects or ambient HTTP proxies.
Batches are capped at256KiB and responses are not buffered or logged. Optional
HMAC-SHA256 signing covers `<X-Frog-Timestamp>.<exact-body>`; verify with the shared
key, reject timestamps outside your freshness window and deduplicate event IDs.
Keys are32–4096 bytes in regular owner-only files; contents never enter the DB.

Command hooks use fixed argv with an absolute executable, JSON stdin, no shell
interpolation, no inherited application secrets, suppressed stdout/stderr and a
five-second timeout. They require `--allow-shell-hooks`, or `allow_shell: true`
in an owner-only local config for automatic delivery. Commands are **trusted
code, not sandboxed**; interpreters can execute anything the user explicitly
configured. Do not store secrets in argv. `FROG_NO_HOOKS=1` prevents ordinary
recursive automatic invocation; a malicious program can ignore it. Child-tree
containment and stronger process/network deadlines are candidates for Rust.

Registered delivery is at-least-once. Success advances the cursor monotonically;
failure preserves it for `frog hook dispatch`. Concurrent dispatch can duplicate
delivery, so receivers must deduplicate. Hook failure never rolls back committed
task work and is reported separately on stderr. One-shot overrides have no
durable retry cursor; register a hook when retries are required. There is no
silent partial one-shot delivery: a batch over100 events is refused, with a
registered cursor-based hook recommended instead. There is no
always-running retry daemon. MCP/direct store callers still dispatch explicitly.

Python's urllib timeout is a socket timeout, **not a full DNS/TLS/write/read
deadline**. No claim of complete I/O hardening or independent security review.
Rust's strongest first pilot is this adapter boundary, preserving these schemas,
registry/cursor semantics and Python's coordination authority.
