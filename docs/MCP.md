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
