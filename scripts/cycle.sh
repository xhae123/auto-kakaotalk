#!/usr/bin/env bash
# One cycle of the main loop. Stateless wrapper; heavy lifting in python.
#
# Verbs:
#   check        -> adapter health + DB init + recovery. Run once at session start.
#   poll         -> ingest new inbound; print list of pending inbound as JSON.
#
# This script never calls the LLM. Drafting happens in the Claude session
# based on what `poll` emits; Claude then calls send.py.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY() { python3 "$SKILL_DIR/scripts/$1" "${@:2}"; }

verb="${1:-poll}"

case "$verb" in
  check)
    # Full prerequisite diagnosis. Non-zero exit means something's missing;
    # the JSON on stdout tells the Claude session exactly what.
    if ! "$SKILL_DIR/scripts/doctor.sh"; then
      exit 1
    fi
    PY db.py init >/dev/null
    PY db.py recover >/dev/null
    echo '{"ok":true}'
    ;;

  poll)
    PY poll.py >/dev/null
    PY db.py list-pending --limit 50
    ;;

  *)
    echo "usage: cycle.sh {check|poll}" >&2
    exit 2
    ;;
esac
