#!/usr/bin/env bash
# Install the codex-usage-epd launchd agent for the current user.
# Fills the __REPO__ placeholder in the plist template with the actual
# checkout path (works for any username / install location).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_IN="$REPO/deploy/com.codex-usage-epd.plist.in"
PLIST_OUT="$HOME/Library/LaunchAgents/com.codex-usage-epd.plist"

mkdir -p "$REPO/logs"

sed "s|__REPO__|$REPO|g" "$PLIST_IN" > "$PLIST_OUT"
echo "installed: $PLIST_OUT"

if launchctl print "gui/$(id -u)/com.codex-usage-epd" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/com.codex-usage-epd" || true
fi
launchctl bootstrap "gui/$(id -u)" "$PLIST_OUT"
echo "launchd: bootstrapped com.codex-usage-epd"