#!/usr/bin/env zsh
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v rem >/dev/null 2>&1; then
  echo "rem is not installed. Install it first: curl -fsSL https://rem.sidv.dev/install | bash"
  exit 1
fi

mkdir -p data
tmp_file="$(mktemp)"

rem export --incomplete --format json > "$tmp_file"
python3 -m json.tool "$tmp_file" > data/reminders.json
rm -f "$tmp_file"

scripts/export_application_wiki_snapshot.sh

git add data/reminders.json data/application_wiki_snapshot.json
if git diff --cached --quiet; then
  echo "Apple Reminders export unchanged."
else
  git commit -m "Update Apple Reminders export"
  git push
fi
