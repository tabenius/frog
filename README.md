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

## Command surface

Strict grammar: `frog <command> <subcommand> [args]`. No positional
guessing — there is no bare `frog <repo> <action>` or `frog <action>`
shorthand. A repo is always addressed under `frog repo …`.

- `frog db migrate` / `frog db schema` — AGENTS.db schema (was `frog init …`)
- `frog new NAME` — scaffold a repo/draft (was the overloaded `frog init NAME`)
- `frog repo list` / `frog repo list -l`
- `frog repo info REPO`
- `frog repo build [REPO]` (and `test|lint|clean|check|verify|diff|status|scan|targets|doctor|artifacts|artifact-stale`; REPO defaults to the cwd repo)
- `frog repo task list --repo REPO`
- `frog repo discover [--root PATH]` (`sync` is an alias; `detect` aliases `scan`)
- `frog task create …` / `frog task list [--repo REPO]`
- `frog lock acquire …`
- `frog status` — workspace summary (now reachable; previously shadowed by the repo `status` action)
- `frog log` / `frog log --follow` (there is no `frog log tail`)
- `frog unit discover [--repo REPO]` / `frog unit list`
- `frog config info` / `frog config path fish`
- `frog completion bash` / `frog completion fish`
- `frog mcp tools` / `frog mcp serve`

Every `--repo-ref` flag is now spelled `--repo`. Use `--workspace NAME`
(not a `workspace:repo` first token) to target another box.

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
