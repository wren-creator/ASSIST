#!/usr/bin/env bash
# ASSIST hook shim. Wired into ~/.claude/settings.json for SessionStart,
# UserPromptSubmit, PostToolUse, Notification, Stop, SessionEnd.
#
# Claude Code pipes the event JSON on stdin. We forward it to the local ASSIST
# host and get out of the way immediately: the curl is backgrounded with a 1s
# cap and we always exit 0, so a slow or down host never stalls or blocks a
# Claude Code turn.
#
# The host wants a bearer token. ASSIST_TOKEN wins, else ~/.assist/token (the
# host creates it on first start). If neither exists we still send, the host
# answers 401, and nothing breaks. The header goes to curl through a file
# descriptor, not argv, so the token never shows up in `ps`.

PORT="${ASSIST_PORT:-8787}"
TOKEN="${ASSIST_TOKEN:-$(cat "${ASSIST_HOME:-$HOME/.assist}/token" 2>/dev/null)}"
payload="$(cat)"

(
  printf '%s' "$payload" | curl -sS --max-time 1 -X POST \
    -H 'Content-Type: application/json' \
    -H @<(printf 'Authorization: Bearer %s' "$TOKEN") \
    --data-binary @- \
    "http://127.0.0.1:${PORT}/hook" >/dev/null 2>&1
) &

exit 0
