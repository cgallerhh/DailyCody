#!/usr/bin/env zsh
set -euo pipefail

cd "$(dirname "$0")/.."

repo_dir="$(pwd)"
label="com.dailycody.reminders-export"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$HOME/Library/Logs/DailyCody"

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir"

cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>$repo_dir/scripts/export_apple_reminders_if_window.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$repo_dir</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>1800</integer>
  <key>StandardOutPath</key>
  <string>$log_dir/reminders-export.log</string>
  <key>StandardErrorPath</key>
  <string>$log_dir/reminders-export.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$plist" >/dev/null 2>&1 || true
launchctl load "$plist"
launchctl start "$label" >/dev/null 2>&1 || true

echo "Installed Daily Cody local export agent."
echo "It checks every 30 minutes and exports Reminders plus the Bewerbungen dashboard snapshot only between 23:59 and 06:59."
echo "Logs: $log_dir/reminders-export.log"
