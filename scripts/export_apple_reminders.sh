#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.."

if ! command -v rem >/dev/null 2>&1; then
  echo "rem is not installed. Install it first: curl -fsSL https://rem.sidv.dev/install | bash"
  exit 1
fi

mkdir -p data
tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

rem_timeout_seconds="${REM_EXPORT_TIMEOUT_SECONDS:-120}"
if ! python3 - "$tmp_file" "$rem_timeout_seconds" <<'PY'; then
import subprocess
import sys

tmp_file = sys.argv[1]
timeout_seconds = int(sys.argv[2])

with open(tmp_file, "w", encoding="utf-8") as fh:
    try:
        subprocess.run(
            ["rem", "export", "--incomplete", "--format", "json"],
            stdout=fh,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=True,
        )
    except subprocess.TimeoutExpired:
        print(
            f"Apple Reminders export timed out after {timeout_seconds} seconds.",
            file=sys.stderr,
        )
        raise SystemExit(124)
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        raise SystemExit(exc.returncode)
PY
  echo "Apple Reminders export failed."
  exit 1
fi
python3 -m json.tool "$tmp_file" > data/reminders.json
python3 - "$tmp_file" <<'PY'
import datetime as dt
import json
import socket
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    reminders = json.load(fh)

status = {
    "exported_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
    "host": socket.gethostname(),
    "source": "Apple Reminders via rem export --incomplete",
    "incomplete_count": len(reminders) if isinstance(reminders, list) else 0,
}
with open("data/reminders_export_status.json", "w", encoding="utf-8") as fh:
    json.dump(status, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
PY

git add data/reminders.json data/reminders_export_status.json

if scripts/export_application_wiki_snapshot.sh; then
  git add data/application_wiki_snapshot.json
else
  echo "Application wiki snapshot failed; continuing with Apple Reminders export." >&2
fi

if git diff --cached --quiet; then
  echo "Apple Reminders export unchanged."
else
  git commit -m "Update Apple Reminders export"
  git push
fi
