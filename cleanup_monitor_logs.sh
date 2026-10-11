#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/home/allanf/scripts/mysql_inno_cluster_monitor/log"
STATE_FILE="$LOG_DIR/state.json"
KEEP_DAYS=5

# Files strictly older than this UTC date are eligible for deletion.
# With KEEP_DAYS=5, today plus the previous 5 calendar days are retained.
CUTOFF_DATE="$(date -u -d "$KEEP_DAYS days ago" +%F)"

# If network-health data is still waiting to be copied into MySQL,
# do not delete the checkpoint file or any later network-health files.
CHECKPOINT_FILE=""
if [[ -f "$STATE_FILE" ]]; then
  CHECKPOINT_FILE="$(python3 - "$STATE_FILE" <<'PY'
import json, sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        s = json.load(f)
    print((s.get("network_health_db_checkpoint") or {}).get("file", ""))
except Exception:
    print("")
PY
)"
fi

echo "Monitor log cleanup"
echo "Log directory : $LOG_DIR"
echo "Keep days     : $KEEP_DAYS"
echo "Cutoff (UTC)  : $CUTOFF_DATE"
[[ -n "$CHECKPOINT_FILE" ]] && echo "DB checkpoint  : $CHECKPOINT_FILE"

extract_date() {
  local name="$1"
  if [[ "$name" =~ _([0-9]{4}-[0-9]{2}-[0-9]{2})\.(jsonl|log)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    printf '%s\n' ""
  fi
}

should_delete() {
  local file="$1"
  local base file_date
  base="$(basename "$file")"
  file_date="$(extract_date "$base")"

  [[ -n "$file_date" ]] || return 1
  [[ "$file_date" < "$CUTOFF_DATE" ]] || return 1

  # Preserve unpersisted network-health history needed for replay.
  if [[ "$base" == network_health_*.jsonl && -n "$CHECKPOINT_FILE" ]]; then
    if [[ "$base" > "$CHECKPOINT_FILE" || "$base" == "$CHECKPOINT_FILE" ]]; then
      return 1
    fi
  fi

  return 0
}

shopt -s nullglob
files=(
  "$LOG_DIR"/monitor_*.log
  "$LOG_DIR"/events_*.jsonl
  "$LOG_DIR"/metrics_*.jsonl
  "$LOG_DIR"/network_health_*.jsonl
)

removed=0
for file in "${files[@]}"; do
  if should_delete "$file"; then
    echo "Deleting: $file"
    rm -f -- "$file"
    removed=$((removed + 1))
  fi
done

echo "Cleanup complete. Files deleted: $removed"

