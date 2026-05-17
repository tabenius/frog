# ragbaz-frog

`ragbaz-frog` is the workspace coordination toolkit for `/data/src`.

It keeps human-readable rules in files and shared machine-readable coordination
state in `/data/src/AGENTS.db`.

## Primary CLI

- `/data/src/ragbaz-frog/bin/frog`

The `frog` command is human-facing by default and supports `--json` for
structured output. It is intended to be suitable for placement on `PATH`.

On first run, `frog` bootstraps a local config at
`~/.config/ragbaz-frog/frog.json` with a default local workspace root inferred
from the installed tree.

## Current command surface

- `frog init migrate`
- `frog completion bash`
- `frog completion fish`
- `frog log tail`
- `frog repo list`
- `frog repo info REPO`
- `frog repo REPO task list`
- `frog task create ...`
- `frog lock acquire ...`
- `frog status`
- `frog config info`
- `frog config path fish`
- `frog mcp tools`
- `frog mcp serve`

## MCP

`frog` can expose a stdio MCP server over the same service layer used by the
CLI:

- `frog mcp tools`
- `frog mcp serve`
- `/data/src/ragbaz-frog/bin/frog-mcp`

The initial MCP tool surface includes repo, unit, task, lock, log, status, and
workspace-listing operations, and it can reuse the configured workspace model
for remote SSH-backed workspaces.

## Notes

- The storage layer uses explicit SQLite migrations.
- The service layer is structured so it can later back an HTTP API.
- The database stores coordination metadata, not full architecture or product
  docs.
