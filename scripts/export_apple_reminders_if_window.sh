#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.."

hour="$(date +%H)"
minute="$(date +%M)"
now_minutes=$((10#$hour * 60 + 10#$minute))
window_start=$((23 * 60 + 59))
window_end=$((6 * 60 + 59))
catchup_max_age_hours="${REMINDERS_CATCHUP_MAX_AGE_HOURS:-24}"

if (( now_minutes >= window_start || now_minutes <= window_end )); then
  echo "Starting Apple Reminders export (scheduled window): $(date '+%Y-%m-%d %H:%M:%S %z')"
  scripts/export_apple_reminders.sh
elif catchup_reason="$(python3 - "$catchup_max_age_hours" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

status_path = Path("data/reminders_export_status.json")
try:
    max_age_hours = max(1.0, float(sys.argv[1]))
except ValueError:
    max_age_hours = 24.0

if not status_path.exists():
    print("status missing")
    raise SystemExit(0)

try:
    data = json.loads(status_path.read_text(encoding="utf-8"))
    exported_at = str(data.get("exported_at") or "").strip()
    exported = dt.datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
except (OSError, ValueError, json.JSONDecodeError) as exc:
    print(f"status unreadable: {exc}")
    raise SystemExit(0)

if exported.tzinfo is None:
    exported = exported.replace(tzinfo=dt.UTC)

age_hours = (dt.datetime.now(dt.UTC) - exported.astimezone(dt.UTC)).total_seconds() / 3600
if age_hours > max_age_hours:
    print(f"status stale: {age_hours:.1f}h old; catch-up max is {max_age_hours:g}h")
    raise SystemExit(0)

raise SystemExit(1)
PY
)"; then
  echo "Starting Apple Reminders export (catch-up: $catchup_reason): $(date '+%Y-%m-%d %H:%M:%S %z')"
  scripts/export_apple_reminders.sh
else
  echo "Outside Apple Reminders export window; export still fresh: $(date '+%Y-%m-%d %H:%M:%S %z')"
fi
