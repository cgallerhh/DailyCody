#!/usr/bin/env zsh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v rem >/dev/null 2>&1; then
  echo "rem is not installed. Install it first: brew install bro3886/tap/rem"
  exit 1
fi

mkdir -p data
tmp_file="$(mktemp)"

rem export --incomplete --format json > "$tmp_file"
python3 -m json.tool "$tmp_file" > data/reminders.json
rm -f "$tmp_file"

git add data/reminders.json
if git diff --cached --quiet; then
  echo "Apple Reminders export unchanged."
else
  git commit -m "Update Apple Reminders export"
  git push
fi
