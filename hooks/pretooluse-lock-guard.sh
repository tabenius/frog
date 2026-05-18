#!/usr/bin/env bash
# ragbaz-frog A3 (II-8) — PreToolUse(Edit|Write) lock guard, agent-aware.
#
# Reads the hook JSON on stdin, extracts the target file, asks
# `frog lock check`, and acts ONLY when a conflicting active lock is held
# by a DIFFERENT agent (resolved via FROG_AGENT, else $USER). Your own
# lock never triggers it.
#
# Wire in /data/src/.claude/settings.local.json (the WORKSPACE):
#   {"hooks":{"PreToolUse":[{"matcher":"Edit|Write","hooks":[
#     {"type":"command",
#      "command":"/data/src/ragbaz-frog/hooks/pretooluse-lock-guard.sh"}]}]}}
#
# Default = warn (systemMessage, never blocks).
# FROG_LOCK_GUARD_BLOCK=1 = deny the edit on a conflicting other-agent lock.
# FROG_DB=/path can target a non-default DB (used by tests).
set -u
FROG="${FROG_BIN:-/data/src/ragbaz-frog/bin/frog}"
ME="${FROG_AGENT:-${USER:-unknown}}"
DBARG=()
[ -n "${FROG_DB:-}" ] && DBARG=(--db "$FROG_DB")

input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: print(""); raise SystemExit
print((d.get("tool_input") or {}).get("file_path",""))' 2>/dev/null)"
[ -z "$file" ] && exit 0

res="$("$FROG" "${DBARG[@]}" --json lock check --scope-key "edit:$file" --file "$file" 2>/dev/null)" || exit 0

other="$(printf '%s' "$res" | ME="$ME" python3 -c 'import json,os,sys
me=os.environ["ME"]
try:
    d=json.load(sys.stdin)
except Exception:
    raise SystemExit
for c in (d.get("conflicts") or []):
    a=c.get("agent_name")
    s=c.get("scope_key","?")
    if a and a!=me:
        print(a+"|"+s)
        break' 2>/dev/null)"
[ -z "$other" ] && exit 0
who="${other%%|*}"; scope="${other##*|}"

if [ "${FROG_LOCK_GUARD_BLOCK:-0}" = "1" ]; then
  printf '{"continue":false,"stopReason":"frog lock guard: %s is locked by %s (%s). Coordinate or have them release first."}\n' "$file" "$who" "$scope"
else
  printf '{"systemMessage":"frog lock guard: %s is locked by %s (%s) — proceeding, but coordinate."}\n' "$file" "$who" "$scope"
fi
exit 0
