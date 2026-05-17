#!/usr/bin/env bash
# ragbaz-frog A3 — PreToolUse(Edit|Write) lock guard.
#
# Reads the hook JSON on stdin, extracts the target file, and asks
# `frog lock check`. If an active lock covers that file, it warns (default)
# or blocks (opt-in).
#
# Wire it in .claude/settings.local.json (the WORKSPACE, not this repo):
#
#   {"hooks":{"PreToolUse":[{"matcher":"Edit|Write","hooks":[
#     {"type":"command",
#      "command":"/data/src/ragbaz-frog/hooks/pretooluse-lock-guard.sh"}]}]}}
#
# Default = warn (systemMessage, never blocks). Set FROG_LOCK_GUARD_BLOCK=1
# to deny the edit when a conflicting lock exists. Harness-only; honest
# limitation: without a per-agent id convention it flags ANY active lock
# covering the file, not strictly "another agent's".
set -u
FROG="${FROG_BIN:-/data/src/ragbaz-frog/bin/frog}"
input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("file_path",""))' 2>/dev/null)"
[ -z "$file" ] && exit 0

res="$("$FROG" --json lock check --scope-key "edit:$file" --file "$file" 2>/dev/null)" || exit 0
n="$(printf '%s' "$res" | python3 -c 'import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print(0); raise SystemExit
print(len(d.get("conflicts") or []))' 2>/dev/null)"
[ "${n:-0}" -eq 0 ] && exit 0

who="$(printf '%s' "$res" | python3 -c 'import json,sys
d=json.load(sys.stdin)
c=(d.get("conflicts") or [{}])[0]
print(c.get("agent_name","?"), c.get("scope_key","?"))' 2>/dev/null)"

if [ "${FROG_LOCK_GUARD_BLOCK:-0}" = "1" ]; then
  printf '{"continue":false,"stopReason":"frog lock guard: %s is under an active lock (%s). Coordinate or `frog lock release` first."}\n' "$file" "$who"
else
  printf '{"systemMessage":"frog lock guard: %s is under an active lock (%s) — proceeding, but coordinate."}\n' "$file" "$who"
fi
exit 0
