#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.."

lock_dir="${TMPDIR:-/tmp}/daily-cody-reminders-export.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  echo "Apple Reminders export already running; skipping duplicate invocation."
  exit 0
fi
tmp_file=""
trap 'rm -rf "$lock_dir"; if [[ -n "$tmp_file" ]]; then rm -f "$tmp_file"; fi' EXIT

if ! command -v rem >/dev/null 2>&1; then
  echo "rem is not installed. Install it first: curl -fsSL https://rem.sidv.dev/install | bash"
  exit 1
fi

mkdir -p data
tmp_file="$(mktemp)"

git_remote="${REMINDERS_GIT_REMOTE:-origin}"
git_branch="${REMINDERS_GIT_BRANCH:-$(git branch --show-current 2>/dev/null || true)}"
if [[ -z "$git_branch" ]]; then
  git_branch="main"
fi

git_can_sync() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 && git remote get-url "$git_remote" >/dev/null 2>&1
}

sync_with_remote() {
  if ! git_can_sync; then
    echo "Git remote sync unavailable; continuing with local export."
    return 0
  fi
  echo "Syncing Apple Reminders export branch with $git_remote/$git_branch"
  if ! git fetch "$git_remote" "$git_branch"; then
    echo "Apple Reminders export warning: git fetch failed; local export will continue." >&2
    return 0
  fi
  if ! git rebase --autostash "$git_remote/$git_branch"; then
    echo "Apple Reminders export failed: could not rebase local branch onto $git_remote/$git_branch." >&2
    return 1
  fi
}

push_with_retry() {
  if ! git_can_sync; then
    echo "Git remote push unavailable; export remains local."
    return 0
  fi
  if git push "$git_remote" "HEAD:$git_branch"; then
    return 0
  fi
  echo "Apple Reminders export push failed; syncing with remote and retrying once." >&2
  if ! sync_with_remote; then
    return 1
  fi
  git push "$git_remote" "HEAD:$git_branch"
}

sync_with_remote

if ! git diff --cached --quiet; then
  echo "Apple Reminders export failed: refusing to include pre-existing staged changes." >&2
  exit 1
fi

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
  push_with_retry
fi
