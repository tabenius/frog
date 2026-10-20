# frog as an MCP server

frog exposes its coordination operations over the Model Context
Protocol so agents (Claude, Codex, …) coordinate *through* frog
instead of shelling out.

    frog mcp serve     # stdio JSON-RPC MCP server
    frog mcp tools     # list the exposed tools

Point your agent runtime's MCP client at `frog mcp serve`. The agent
can then claim/finish tasks, query `task next`, inspect the board, and
acquire/inspect locks as MCP tool calls — the same operations as the
CLI, transactional against the shared `AGENTS.db`.

Identity: set `FROG_AGENT` (e.g. `claude-$(hostname -s)`) and
optionally `FROG_SESSION` so the event log and lock ownership
attribute correctly. `frog setup <claude|codex>`
generate the config/hook/MCP wiring for a repo.

This is frog's intended integration surface: infrastructure an agent
stands on, invisibly — not another tool it has to drive.

## Foreground HTTP

```bash
frog serve mcp --http
frog serve mcp --http --listen 127.0.0.1:8081 --foreground --json
frog serve mcp --http --listen 0.0.0.0:8081 --fg --clf
```

Foreground is the default; `--foreground` and `--fg` are equivalent. The
default binding is `127.0.0.1:8081`, endpoint `/mcp`. `--listen` accepts an
IP:port, including `[::1]:8081`. Select a backend explicitly with global
`--db PATH` or the existing model configuration; transport failures never
silently switch backends. `FROG_CONFIG` selects the config file.

Set `FROG_MCP_TOKEN` before binding outside loopback. Clients send it as
`Authorization: Bearer …`; `--token-env NAME` selects another environment
variable. A token set on loopback is enforced there too. Use a trusted TLS
proxy or tunnel for off-machine access. `--allow-origin ORIGIN` explicitly
allows an additional client Origin. Loopback Host headers are checked.

Clients POST JSON-RPC with `Content-Type: application/json` and
`Accept: application/json, text/event-stream`. The stateless server returns
JSON, not SSE; GET/DELETE are unsupported. Requests require Content-Length,
are capped at 64 KiB, and chunked transfer encoding is refused. Supported
protocol headers are checked; initialization negotiates a supported version.
Notifications return 202 with no body. SIGTERM stops the foreground process.

Stdout is access logs only: `--json` produces JSONL and `--clf` produces
common logfile format (also the default). The flags are mutually exclusive.
JSONL includes a startup event, then UTC datetime, remote address, HTTP
method/path/status/bytes/duration, known MCP method and known tool name.
CLF startup information goes to stderr. Arguments, results, query strings
and bearer credentials are omitted from access logs.

## Shared native/hosted catalog

Eight read tools share canonical descriptions and input schemas with Dash:
`dash_list_instances`, `dash_read_instance`, `dash_read_view`,
`dash_read_frog`, `dash_read_audit`, `frog_task_list`, `frog_status`, and
`frog_log_tail`. Native Frog retains its write tools. Its `dash_*` aliases
describe the selected local workspace with stable local instance identity;
Rebekah-only views report unavailable. Dash exposes only read tools backed
by membership-checked snapshots and account/instance audit views.

Authority lives in `src/ragbaz_frog/mcp_contract.json`. Synchronize/check:

```bash
python3 bin/sync-mcp-contract.py --dash ../ragbaz-dash
python3 bin/sync-mcp-contract.py --dash ../ragbaz-dash --check
```

Dash CI compares against a pinned public Frog commit; Frog's standalone CI
tests the authoritative wire schemas without private Dash access. Contract
changes must update generated artifacts, the digest, and Dash's Frog pin together;
review against the actual wire schemas, not just source JSON. Catalog/API
versions are separate from date-based persisted data versions; see
[the migration plan](MODEL-MIGRATION.md).

## Pre-edit hook: refuse silent collisions

`frog lock check-file <path>` answers the question "may THIS agent
edit THIS file right now?" with three outcomes encoded in the exit
code:

  - `0` — COVERED: this agent holds an active lock that includes the file
  - `1` — CONFLICT: another agent holds an active lock that includes the file
  - `2` — UNCOVERED: no active lock covers the file (advisory)

A minimal Claude Code `.claude/settings.json` PreToolUse hook that
blocks edits on conflict and warns on uncovered files:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.file_path // empty' | { read -r f; [ -n \"$f\" ] && frog lock check-file \"$f\"; }"
      }]
    }]
  }
}
```

The hook fails closed: if `frog lock check-file` exits non-zero, the
tool call is blocked with the holder identity printed to stderr. For
advisory-only mode, use `frog lock audit --warn` (exits 0 even with
findings).

This closes the silent-collision class observed in real multi-agent
sessions: editing a file in a shared working tree where another agent
holds a conflicting lock no longer fails silently.
