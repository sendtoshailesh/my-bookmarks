#!/bin/bash
# ─────────────────────────────────────────────────────────────────
# sync-readlist.sh — Scan reading list & generate content with notifications
#
# Usage:
#   ./sync-readlist.sh              # scan only
#   ./sync-readlist.sh generate 3   # generate for top 3
# ─────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIPELINE="$SCRIPT_DIR/reading_pipeline.py"
ACTION="${1:-scan}"
TOP="${2:-}"

notify() {
  local title="$1" message="$2" sound="${3:-default}"
  osascript -e "display notification \"$message\" with title \"$title\" sound name \"$sound\"" 2>/dev/null || true
}

if [[ ! -f "$PIPELINE" ]]; then
  notify "📖 Reading Pipeline" "❌ reading_pipeline.py not found!" "Basso"
  exit 1
fi

notify "📖 Reading Pipeline" "⏳ Running $ACTION..."

if [[ "$ACTION" == "generate" && -n "$TOP" ]]; then
  if OUTPUT=$(python3 "$PIPELINE" generate --top "$TOP" 2>&1); then
    notify "📖 Reading Pipeline" "✅ Content generated for top $TOP items!" "Glass"
    echo "$OUTPUT"
  else
    notify "📖 Reading Pipeline" "❌ Generation failed!" "Basso"
    echo "$OUTPUT" >&2
    exit 1
  fi
elif [[ "$ACTION" == "scan" ]]; then
  if OUTPUT=$(python3 "$PIPELINE" scan 2>&1); then
    ITEMS=$(echo "$OUTPUT" | grep -oE '[0-9]+ items' | head -1)
    notify "📖 Reading Pipeline" "✅ Scanned ${ITEMS:-reading list}!" "Glass"
    echo "$OUTPUT"
  else
    notify "📖 Reading Pipeline" "❌ Scan failed!" "Basso"
    echo "$OUTPUT" >&2
    exit 1
  fi
else
  python3 "$PIPELINE" "$ACTION" ${TOP:+"$TOP"}
fi
