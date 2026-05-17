# frog hooks

## pretooluse-lock-guard.sh  (A3)

A PreToolUse(Edit|Write) bridge that turns frog's advisory locks into a
*noticed* (opt-in: *enforced*) signal for harness-run agents.

It is intentionally **not** auto-enabled: a blocking PreToolUse hook
affects every agent session in the workspace, so flipping it on is an
explicit operator decision.

Enable (warn-only) by adding to `/data/src/.claude/settings.local.json`:

```json
{"hooks":{"PreToolUse":[{"matcher":"Edit|Write","hooks":[
  {"type":"command",
   "command":"/data/src/ragbaz-frog/hooks/pretooluse-lock-guard.sh"}]}]}}
```

- Default: prints a `systemMessage` warning, never blocks.
- `FROG_LOCK_GUARD_BLOCK=1`: denies the edit on a conflicting lock.

Honest limitation: precise "another agent's lock" attribution needs a
per-agent id convention (agents currently share `$USER`); today it flags
any active lock covering the file. Tightening this is future work.
