#!/usr/bin/env bash
# Diagnose prerequisites. Emit one JSON object per check on stdout.
# Exit 0 if all pass, 1 if any fail.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTH="$HERE/adapters/_kakao_auth.py"

report() {
  # $1=name  $2=ok|fail  $3=detail
  printf '{"check":"%s","ok":%s,"detail":%s}\n' \
    "$1" "$2" "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$3")"
}

fail_count=0

# 1. macOS
if [[ "$(uname)" == "Darwin" ]]; then
  report "macos" true "$(sw_vers -productVersion 2>/dev/null || echo unknown)"
else
  report "macos" false "not macOS; this skill only runs on macOS"
  fail_count=$((fail_count+1))
fi

# 2. KakaoTalk.app installed
if [[ -d "/Applications/KakaoTalk.app" ]]; then
  report "kakaotalk_app" true "/Applications/KakaoTalk.app"
else
  report "kakaotalk_app" false "KakaoTalk for Mac not installed (mas install 869223134)"
  fail_count=$((fail_count+1))
fi

# 3. cliclick (used by _send.applescript for UI automation clicks)
if command -v cliclick >/dev/null 2>&1; then
  report "cliclick" true "$(command -v cliclick)"
else
  report "cliclick" false "cliclick missing; brew install cliclick"
  fail_count=$((fail_count+1))
fi

# 4. kakaocli brew binary (used by _kakao_auth.py for DB queries)
if command -v kakaocli >/dev/null 2>&1; then
  report "kakaocli" true "$(command -v kakaocli)"
else
  report "kakaocli" false "kakaocli missing; brew install silver-flight-group/tap/kakaocli"
  fail_count=$((fail_count+1))
fi

# 5. Local auth resolution (plist + key + cache)
if python3 "$AUTH" auth >/dev/null 2>&1; then
  report "kakao_auth" true "auth cached"
else
  report "kakao_auth" false "auth failed (Full Disk Access missing, or KakaoTalk not logged in)"
  fail_count=$((fail_count+1))
fi

# 6. Accessibility
if osascript -e 'tell application "System Events" to count processes' >/dev/null 2>&1; then
  report "accessibility" true "granted"
else
  report "accessibility" false "terminal lacks Accessibility (System Settings > Privacy & Security > Accessibility)"
  fail_count=$((fail_count+1))
fi

exit $(( fail_count > 0 ? 1 : 0 ))
