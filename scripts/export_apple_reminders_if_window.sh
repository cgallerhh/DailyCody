#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.."

hour="$(date +%H)"
minute="$(date +%M)"
now_minutes=$((10#$hour * 60 + 10#$minute))
window_start=$((23 * 60 + 59))
window_end=$((6 * 60 + 59))

if (( now_minutes >= window_start || now_minutes <= window_end )); then
  echo "Starting Apple Reminders export: $(date '+%Y-%m-%d %H:%M:%S %z')"
  scripts/export_apple_reminders.sh
else
  echo "Outside Apple Reminders export window: $(date '+%Y-%m-%d %H:%M:%S %z')"
fi
