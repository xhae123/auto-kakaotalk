#!/usr/bin/env bash
# Platform boundary for KakaoTalk. Self-contained.
#
# Verbs:
#   check                          -> exit 0 if local auth resolves
#   resolve                        -> stdout: chat list JSON
#   history <chat> <limit>         -> stdout: messages JSON (oldest->newest)
#   poll <chat> <since_iso>        -> stdout: new messages JSON
#   send <display_name> <text>     -> exit 0 on success
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTH="$HERE/_kakao_auth.py"
SENDER="$HERE/_send.applescript"

verb="${1:-}"
shift || true

case "$verb" in
  check)
    python3 "$AUTH" auth >/dev/null
    ;;

  resolve)
    python3 "$AUTH" chats --limit 200 --json
    ;;

  history)
    chat="${1:?chat required}"
    limit="${2:-500}"
    python3 "$AUTH" messages --chat "$chat" --limit "$limit" --json
    ;;

  poll)
    chat="${1:?chat required}"
    since="${2:?since ISO required}"
    python3 "$AUTH" messages --chat "$chat" --since "$since" --json
    ;;

  send)
    display_name="${1:?display_name required}"
    text="${2:?text required}"
    osascript "$SENDER" "$display_name" "$text" send
    ;;

  *)
    echo "unknown verb: $verb" >&2
    echo "usage: kakao.sh {check|resolve|history|poll|send} ..." >&2
    exit 2
    ;;
esac
