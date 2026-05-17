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

## Multi-agent coordination (evolution)

frog's differentiator is the coordination/scheduling/forensics layer:

- `frog lock audit [--repo R] [--agent N]` — flag working-tree changes
  not covered by your active lock (advisory → *noticed*).
- `frog lock reap` / `frog lock list --status {active,stale,released,all}`.
- `frog repo affected [--since REF]` / `frog repo build --affected`
  (input-fingerprinted `target_runs` cache: unchanged targets are skipped).
- `frog repo dep add DEPENDENT DEPENDENCY` — declared cross-repo edges;
  upstream changes fan out to dependents.
- `frog task next [--agent N]` — highest-ROI unblocked slice (deps,
  conflicts, locks, ownership aware). The scheduler an issue tracker isn't.
- `frog log why SLUG` / `frog log blame FILE` — causality/forensics.
- `frog sync pull WS` / `frog sync list` — read-only event mirror of
  another workspace (single-writer-per-DB; safe cross-box visibility).
- `connect()` enforces WAL + local-FS-only AGENTS.db (network-FS sqlite
  refused; use `--workspace` which RPCs over SSH).
- Runner delegation: a declared Taskfile/justfile/mise outranks
  re-derived make/npm targets — frog orchestrates, the runner builds.
- Opt-in `hooks/pretooluse-lock-guard.sh` bridges locks to the harness.

See `PLAN.md` for the phased rollout; `tests/` is a stdlib unittest
suite (`python3 -m unittest discover -s tests`).
