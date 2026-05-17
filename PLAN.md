# ragbaz-frog evolution plan

> Executed task-by-task. Each phase = code + `unittest` + one commit + tick here.
> Through-line: stop competing with build tools / issue trackers on their turf;
> become the multi-agent coordination / scheduling / forensics layer above them.

Baseline: `6bb0f7e` (import) -> `5d282f6` (strict-grammar redesign).

## Phase 0 - foundations
- [x] C1: `connect()` sets `journal_mode=WAL`, `busy_timeout=5000`,
      `synchronous=NORMAL` (robust local multi-writer; common case).
- [x] Test harness: stdlib `unittest` under `tests/`, zero new deps,
      `python3 -m unittest discover -s tests` green.

## Phase A - locks: advisory -> noticed
- [x] A1 `frog lock audit [--repo R]`: git-dirty files not covered by an
      active lock held by the current/declared agent -> `lock.audit.uncovered`
      findings + nonzero exit. (Honest reading of AGENTS.md rule 6.)
- [x] A2 lease reaping made first-class: `frog lock reap`, `lock list
      --status active|stale|expired|all`, expose `_mark_stale_locks` as an
      explicit op + `lock.expired` distinct from `lock.stale`.
- [x] A3 hook bridge: `PreToolUse(Edit|Write)` hook running
      `frog lock check --file <path> --agent <me>`; warn/block on a
      conflicting active lock held by another agent. Opt-in, harness-only.

## Phase B - affected-graph: make `repo build` cheap
- [x] B1 migration `004_target_runs.sql` + input fingerprint; `repo_run`
      skips a target whose last successful `input_hash` is unchanged.
- [x] B2 implement the missing `store.repo_diff` (referenced by
      `frog repo diff`, currently an AttributeError), then
      `frog repo affected [--since REF]` and `frog repo build --affected`.
- [ ] B3 migration `005_repo_deps.sql` (declared edges) +
      `frog repo dep add A B` + `affected` fan-out across declared deps.

## Phase "do it better" - lean into the niche
- [ ] Runner delegation: detect Taskfile/justfile/mise/moon, emit
      delegating targets (high confidence, `runner` recorded).
- [ ] Agent scheduler: `frog task next --agent X` = highest-ROI unblocked
      slice (deps satisfied, no active conflict, repo not locked by another
      agent, priority-ordered).
- [ ] Causality: `frog log why <task-slug>` + `frog log blame <file>`.

## Phase Z - surface sync
- [ ] Update completion (bash+fish), help epilog, README,
      /data/src/AGENTS.md, MCP tool list. Final `unittest` green.
