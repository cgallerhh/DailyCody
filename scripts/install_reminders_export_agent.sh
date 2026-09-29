#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.."

repo_dir="$(pwd)"
label="com.dailycody.reminders-export"
plist="$HOME/Library/LaunchAgents/$label.plist"
log_dir="$HOME/Library/Logs/DailyCody"
runtime_dir="$HOME/Library/Application Support/DailyCody"
runtime_repo_dir="$runtime_dir/repo"
runtime_bin_dir="$runtime_dir/bin"
runtime_export_script="$runtime_dir/export_apple_reminders.sh"
runtime_window_script="$runtime_dir/export_apple_reminders_if_window.sh"
runtime_rem="$runtime_bin_dir/rem"

mkdir -p "$HOME/Library/LaunchAgents" "$log_dir" "$runtime_bin_dir"

remote_url="$(git remote get-url origin)"
if [[ ! -d "$runtime_repo_dir" ]]; then
  git clone --branch main --single-branch "$remote_url" "$runtime_repo_dir"
elif [[ "$(git -C "$runtime_repo_dir" rev-parse --show-toplevel 2>/dev/null || true)" != "$runtime_repo_dir" ]]; then
  echo "Existing Daily Cody runtime repository is invalid: $runtime_repo_dir" >&2
  exit 1
elif [[ "$(git -C "$runtime_repo_dir" remote get-url origin)" != "$remote_url" ]]; then
  echo "Daily Cody runtime repository points to a different Git remote: $runtime_repo_dir" >&2
  exit 1
fi
git -C "$runtime_repo_dir" config user.name "Daily Cody"
git -C "$runtime_repo_dir" config user.email "christian.galler+cody@gmail.com"

cp "$repo_dir/scripts/export_apple_reminders.sh" "$runtime_export_script"
cp "$repo_dir/scripts/export_apple_reminders_if_window.sh" "$runtime_window_script"
chmod 755 "$runtime_export_script" "$runtime_window_script"

rem_source="$(command -v rem || true)"
if [[ -z "$rem_source" || ! -x "$rem_source" ]]; then
  echo "rem is not installed. Install it first: curl -fsSL https://rem.sidv.dev/install | bash" >&2
  exit 1
fi
if [[ ! -x "$runtime_rem" || "${REMINDERS_REFRESH_CLI:-false}" == "true" ]]; then
  cp "$rem_source" "$runtime_rem"
  chmod 755 "$runtime_rem"
  codesign --force --sign - --identifier com.dailycody.reminders-cli "$runtime_rem"
fi

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
    <string>$runtime_window_script</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$runtime_repo_dir</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>DAILY_CODY_REPO_DIR</key>
    <string>$runtime_repo_dir</string>
    <key>DAILY_CODY_EXPORT_SCRIPT</key>
    <string>$runtime_export_script</string>
    <key>REMINDERS_CLI</key>
    <string>$runtime_rem</string>
  </dict>
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

launchctl bootout "gui/$(id -u)/$label" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl kickstart -k "gui/$(id -u)/$label" >/dev/null 2>&1 || true

echo "Installed Daily Cody local export agent."
echo "The runner, Git checkout, and a stable Reminders CLI copy are outside iCloud Drive at: $runtime_dir"
echo "It checks every 30 minutes, exports between 23:59 and 06:59, retries pending pushes, and runs a catch-up export whenever the last export is older than 1 hour."
echo "Logs: $log_dir/reminders-export.log"
