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
