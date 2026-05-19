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
- `frog repo build [REPO]` (and `package|dist|test|lint|clean|check|verify|diff|status|scan|targets|doctor|artifacts|artifact-stale`; REPO defaults to the cwd repo)
- `frog repo task list --repo REPO`
- `frog repo discover [--root PATH]` (`sync` is an alias; `detect` aliases `scan`)
- `frog task create …` / `frog task list [--repo REPO]`
- `frog lock acquire …`
- `frog provider sync --source asana|linear|jira --config-file FILE --direction pull|push|both`
- `frog hook add URL` / `frog hook list` / `frog hook remove ID`
- `frog hook dispatch [--id ID] [--limit N]` — POST new event-log batches to enabled hooks
- `frog hook digest [--limit N] [--repo REPO]` — emit a Markdown event digest
- `frog status` — workspace summary (now reachable; previously shadowed by the repo `status` action)
- `frog log` / `frog log --follow` (there is no `frog log tail`)
- `frog unit discover [--repo REPO]` / `frog unit list`
- `frog config info` / `frog config host …` / `frog config workspace …`
- `frog config coordinator show` / `frog config coordinator set WORKSPACE`
- `frog box whoami` — show the stable identity stamped onto locks and aliases
- `frog box join [user@]host[:/path/AGENTS.db]` — learn a peer box over SSH
- `frog box peers` — list registered peer boxes
- `frog whereis REPO_KEY` — resolve a logical repo key to paths on configured boxes
- `frog config path fish`
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
- `frog config coordinator set WS` designates the single write authority for
  tasks and locks. Default/current workspace writes to task and lock state
  route there over the existing SSH workspace seam; explicit `--workspace` and
  explicit `--db` still mean exactly what they say.
- `frog whereis REPO_KEY` resolves the local path and asks configured remote
  workspaces in non-recursive local-only mode, so agents can find the same
  logical repo on different boxes.
- `frog box join [user@]host[:/path/AGENTS.db]` asks the peer for its
  `box_id` and repo-key map over SSH, records shared repo aliases locally, and
  registers the peer for later sync/scheduling flows. It does not share a DB,
  move source code, or start a daemon.
- `frog provider sync` runs concrete Asana, Linear, and Jira adapters on top of
  the normalized provider contract. `--direction pull|push|both` controls sync
  direction; provider JSON supplies credentials and status round-trip mappings
  such as Asana enum option GIDs, Linear state IDs, or Jira transition IDs.
- `frog hook ...` publishes event-log batches to human notification systems.
  Dispatch is explicit (`frog hook dispatch`) so ordinary coordination writes
  do not unexpectedly perform network calls; `frog hook digest` provides a
  terminal-friendly Markdown summary of recent events.
- `connect()` enforces WAL + local-FS-only AGENTS.db (network-FS sqlite
  refused; use `--workspace` which RPCs over SSH).
- Runner delegation: a declared Taskfile/justfile/mise outranks
  re-derived make/npm targets — frog orchestrates, the runner builds.
- Opt-in `hooks/pretooluse-lock-guard.sh` bridges locks to the harness.

See `PLAN.md` for the phased rollout; `tests/` is a stdlib unittest
suite (`python3 -m unittest discover -s tests`).

### Phase II (consolidation)

- `frog agent whoami|register` — first-class identity (FROG_AGENT) so
  peers on the same OS user are distinct; flows into locks/tasks/audit.
- `frog task claim <slug>` / `frog task finish <slug>` — composed
  workflow: claim = assign+lock+in_progress; finish = affected
  build/test gate → done + release + report unblocked dependents.
- `frog db gc [--older-than D] [--keep N]` — prune event/target_runs +
  VACUUM. `frog doctor` — self-diagnostic. `frog board [--once]` —
  realtime colored lifecycle board over the event log.
- Concurrency-safe: lock_acquire is atomic (BEGIN IMMEDIATE) — proven
  by a multiprocessing stress test. `--db` now overrides the workspace
  DB (was silently ignored). SSH path testable via an injectable seam.
- `pip install -e .` (pyproject + console_scripts frog / frog-mcp).
