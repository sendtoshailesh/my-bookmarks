#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# sync-bookmarks.sh — One-click bookmark sync with macOS notifications
#
# Usage:
#   ./sync-bookmarks.sh          # sync both browsers
#   ./sync-bookmarks.sh chrome   # sync Chrome only
#   ./sync-bookmarks.sh edge     # sync Edge only
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SYNC_SCRIPT="$SCRIPT_DIR/bookmark_sync.py"
BROWSER="${1:-both}"

# macOS notification helper
notify() {
  local title="$1" message="$2" sound="${3:-default}"
  osascript -e "display notification \"$message\" with title \"$title\" sound name \"$sound\"" 2>/dev/null || true
}

# Preflight checks
if [[ ! -f "$SYNC_SCRIPT" ]]; then
  notify "🔖 Bookmark Sync" "❌ bookmark_sync.py not found!" "Basso"
  echo "ERROR: $SYNC_SCRIPT not found" >&2
  exit 1
fi

if ! command -v python3 &>/dev/null; then
  notify "🔖 Bookmark Sync" "❌ python3 not found!" "Basso"
  echo "ERROR: python3 is required" >&2
  exit 1
fi

# Show starting notification
notify "🔖 Bookmark Sync" "⏳ Syncing $BROWSER bookmarks..."

# Run sync and capture output
START_TIME=$(date +%s)
if OUTPUT=$(python3 "$SYNC_SCRIPT" sync --browser "$BROWSER" 2>&1); then
  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))

  # Extract bookmark count from output
  TOTAL=$(echo "$OUTPUT" | grep -oE '[0-9,]+ unique' | head -1 | tr -d ',')
  DUPES=$(echo "$OUTPUT" | grep -oE '[0-9,]+ duplicates' | head -1 | tr -d ',')
  
  if [[ -n "$TOTAL" ]]; then
    MSG="✅ $TOTAL bookmarks synced (${DUPES:-0} dupes removed) in ${DURATION}s"
  else
    MSG="✅ Sync completed in ${DURATION}s"
  fi
  
  # Check if push happened (look for push confirmation in output)
  if echo "$OUTPUT" | grep -q "Pushed to GitHub"; then
    MSG="$MSG — published to Pages 🚀"
  fi

  notify "🔖 Bookmark Sync" "$MSG" "Glass"
  echo "$MSG"
  echo "$OUTPUT"
else
  notify "🔖 Bookmark Sync" "❌ Sync failed! Check terminal for details." "Basso"
  echo "ERROR: Sync failed" >&2
  echo "$OUTPUT" >&2
  exit 1
fi
