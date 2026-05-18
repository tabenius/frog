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

## Phase C - multi-writer hardening
- [x] C2: `connect()` guard refuses a `--db` on a non-local filesystem
      (nfs/fuse.sshfs) -> point at `--workspace` (which RPCs) instead.
      Makes "DB is local to its host" an enforced invariant.
- [x] C3: `frog sync pull <workspace>` streams remote events since the
      last cursor into a read-only `event_mirror` (single-writer-per-DB,
      many-reader-via-replay). migration `006_event_mirror.sql`.

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
- [x] B3 migration `005_repo_deps.sql` (declared edges) +
      `frog repo dep add A B` + `affected` fan-out across declared deps.

## Phase "do it better" - lean into the niche
- [x] Runner delegation: detect Taskfile/justfile/mise/moon, emit
      delegating targets (high confidence, `runner` recorded).
- [x] Agent scheduler: `frog task next --agent X` = highest-ROI unblocked
      slice (deps satisfied, no active conflict, repo not locked by another
      agent, priority-ordered).
- [x] Causality: `frog log why <task-slug>` + `frog log blame <file>`.

## Phase Z - surface sync
- [x] Update completion (bash+fish), help epilog, README,
      /data/src/AGENTS.md, MCP tool list. Final `unittest` green.

---

# Phase II - consolidation (identity, workflow, proof, board)

- [x] II-1 Agent identity: store.current_agent()/current_session()
      (env FROG_AGENT/FROG_SESSION, else $USER + host:pid);
      `frog agent whoami|register`; thread identity defaults into
      lock/task/audit/scheduler instead of bare $USER.
- [x] II-2 Workflow composition: `frog task claim <slug>` (assign + lock +
      in_progress + event, atomic) and `frog task finish <slug>`
      (affected build/test gate -> done + release + event), reporting
      newly-unblocked dependents.
- [x] II-3 Concurrency stress test: N processes hammering
      lock_acquire / task_claim -> no double-grant, no deadlock.
- [x] II-4 Remote seam: injectable dispatch so sync/workspace paths are
      testable without SSH; tests.
- [ ] II-5 `frog db gc [--older-than D] [--keep N]`: prune event_log /
      event_mirror / target_runs, WAL checkpoint + VACUUM.
- [ ] II-6 `frog doctor`: health over locks/tasks/events/DB size
      (stale locks, deps-on-done-but-blocked data bugs, mirror lag).
- [ ] II-7 Packaging: pyproject.toml + console_scripts (frog, frog-mcp),
      pinned Python floor; bin/ shims kept.
- [ ] II-8 A3 decision: with identity, lock guard enforces only against
      a *different* agent's active lock; still opt-in but now meaningful.
- [ ] II-9 Capstone: `frog board` realtime colored lifecycle board over
      event_log (enters / assigned / status / claimed / finished + which
      dependents unlock), + design writeup.
