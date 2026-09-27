# ragbaz-frog

`ragbaz-frog` is the workspace coordination toolkit for `/data/src`.

It keeps human-readable rules in files and shared machine-readable coordination
state in `/data/src/AGENTS.db`.

## Related projects: WeftMark and Sylvae

Two other repos in this workspace sit near frog's territory. Neither replaces
it today; read this before assuming either one does.

- **[WeftMark](../experiments/WeftMark)** is a *prospective successor* to
  frog's task/lock/coordination domain specifically — not a fork, not a
  rewrite of frog's code. Its own README carries a "From Frog to WeftMark"
  table naming the transition explicitly: frog's tasks/deps/claims become
  WeftMark's Change Sets, frog's file/repo locks become file *and*
  semantic/contract scopes, frog's event log becomes an immutable evidence
  ledger, and so on — frog's lessons made first-class, not thrown away.
  WeftMark is still `prototype` (its own `ragbaz.component.json` says so)
  and has no repo/build/scan/discovery layer at all — it starts from
  evidence and handoff semantics, not from "find and index every repo."
  **Practical answer: use frog for anything repo/task/lock/build/discovery
  today.** WeftMark is worth watching, not switching to, until it grows
  that ground floor.
- **[Sylvae](../experiments/Sylvae)** is unrelated in scope, not a
  coordination tool at all — a portable skill runner that executes a
  `SKILL.md` against a chosen backend (Ollama, Claude Code, Codex,
  OpenCode, or the Anthropic API directly) and logs every run as an
  evidence record. Complementary layer, not competing: frog decides *what
  work exists and who's doing it*; Sylvae is one way a *cheap-tier* model
  could actually execute a well-scoped piece of it. Nothing wires them
  together today (Sylvae's own docs don't mention frog), but frog's MCP
  tool surface and Sylvae's MCP server (`sylvae mcp`, tools
  `sylvae_list_skills`/`sylvae_run_skill`) are both real integration points
  if that's ever worth building.

## Future

`PLAN.md` tracks everything actually committed and shipped — Phase 0
through Phase II are all closed there; there is no open Phase III entry in
it right now, so treat anything below as informed extrapolation, not a
roadmap promise:

- **WeftMark absorbing the task/lock/evidence domain** is the most
  concrete forward signal on file (see above) — not a frog initiative,
  but the reason not to over-invest in frog's own evidence/scope model
  growing much further past where it is today.
- **Hardening the surface every other product actually calls.** This
  review (2026-08-25) found and fixed two bugs that were reachable by any
  caller of `frog repo discover` — including WeftMark or Sylvae sessions,
  or anyone else's `frog_repo_discover` MCP call, not just a human running
  the CLI: a dead `.git`-detection code path that made manifest-less repos
  invisible to discovery, and one malformed manifest anywhere under the
  scanned root aborting discovery for the *entire remaining workspace*.
  Neither was theoretical — both were live, reproducible against this
  actual workspace. The general lesson (audit every MCP-exposed code path
  for "what happens when a peer product hands this something malformed or
  unexpected," not just "does the happy path work") is the kind of pass
  worth repeating periodically, not a one-time fix.
- **`frog repo discover`'s self-registration gap.** Discovering a repo by
  passing its own path as `--root` now works (the `.git` fix above), but
  there's no single obvious command a new repo's owner runs once to
  register-and-forget — `frog repo discover --root <path>` is the answer,
  but it's not documented as *the* onboarding step anywhere prominent.
  Worth a `frog new`-adjacent affordance or a README callout, not solved
  here.

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
- Lock kinds are freeform labels, not an enum. Use stable conventional names
  such as `edit`, `docs`, `build`, `test`, `scan`, or `deploy` so humans can
  read lock lists quickly.
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
  Claim refuses a second active task for the same agent by default; use
  `--allow-parallel` only when the extra slice is intentionally disjoint.
- `frog db gc [--older-than D] [--keep N]` — prune event/target_runs +
  VACUUM. `frog doctor` — self-diagnostic. `frog board [--once]` —
  realtime colored lifecycle board over the event log.
- Concurrency-safe: lock_acquire is atomic (BEGIN IMMEDIATE) — proven
  by a multiprocessing stress test. `--db` now overrides the workspace
  DB (was silently ignored). SSH path testable via an injectable seam.
- `pip install -e .` (pyproject + console_scripts frog / frog-mcp).

## Documentation

- [docs/COMMANDS.md](docs/COMMANDS.md) — full command reference (generated; run `frog docs`)
- [docs/FEDERATION.md](docs/FEDERATION.md) — multi-box model: box identity, join, whereis, cross-box locks
- [docs/MCP.md](docs/MCP.md) — using frog as an MCP server for agents
- [docs/DEPLOY.md](docs/DEPLOY.md) — install, the DB, schema-skew guard, backups, repo move
