#!/usr/bin/env zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

repo_dir="${DAILY_CODY_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$repo_dir"

lock_dir="${TMPDIR:-/tmp}/daily-cody-reminders-export.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  if [[ -f "$lock_dir/pid" ]]; then
    old_pid="$(cat "$lock_dir/pid" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && ! kill -0 "$old_pid" 2>/dev/null; then
      echo "Apple Reminders export found stale lock for pid $old_pid; removing it."
      rm -rf "$lock_dir"
      mkdir "$lock_dir"
    else
      echo "Apple Reminders export already running; skipping duplicate invocation."
      exit 0
    fi
  else
    echo "Apple Reminders export found stale lock without pid; removing it."
    rm -rf "$lock_dir"
    mkdir "$lock_dir"
  fi
fi
echo "$$" > "$lock_dir/pid"
export_started_with_clean_index=false
publish_enabled=true
publish_disabled_reason=""

disable_publish() {
  publish_enabled=false
  if [[ -z "$publish_disabled_reason" ]]; then
    publish_disabled_reason="$1"
  else
    publish_disabled_reason="$publish_disabled_reason; $1"
  fi
}

if git diff --cached --quiet; then
  export_started_with_clean_index=true
else
  staged_check_exit=$?
  if [[ "$staged_check_exit" -eq 1 ]]; then
    disable_publish "pre-existing staged changes"
    echo "Git main-worktree publish skipped for Apple Reminders export: pre-existing staged changes."
  else
    disable_publish "git staged-state check failed"
    echo "Apple Reminders export warning: git staged-state check failed with exit code $staged_check_exit; export will continue." >&2
  fi
fi
cleanup_export() {
  exit_code=$?
  if [[ "$exit_code" -ne 0 && "$export_started_with_clean_index" == "true" ]]; then
    git restore --staged data/reminders.json data/reminders_export_status.json data/application_wiki_snapshot.json >/dev/null 2>&1 || true
  fi
  if [[ -n "$publish_dir" ]]; then
    git worktree remove --force "$publish_dir" >/dev/null 2>&1 || rm -rf "$publish_dir"
  fi
  rm -rf "$lock_dir"
  if [[ -n "$tmp_file" ]]; then
    rm -f "$tmp_file"
  fi
}
tmp_file=""
publish_dir=""
trap cleanup_export EXIT

rem_command="${REMINDERS_CLI:-$(command -v rem || true)}"
if [[ -z "$rem_command" || ! -x "$rem_command" ]]; then
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
current_branch="$(git branch --show-current 2>/dev/null || true)"

if [[ "$publish_enabled" == "true" ]]; then
  if [[ -z "$current_branch" ]]; then
    disable_publish "detached HEAD"
  elif [[ -d .git/rebase-merge || -d .git/rebase-apply ]]; then
    disable_publish "rebase in progress"
  else
    if git_status_output="$(git status --porcelain --untracked-files=no 2>/dev/null)"; then
      blocking_changes="$(
        print -r -- "$git_status_output" |
          awk '{print $2}' |
          grep -Ev '^(data/reminders\.json|data/reminders_export_status\.json|data/application_wiki_snapshot\.json)$' || true
      )"
      if [[ -n "$blocking_changes" ]]; then
        disable_publish "local non-export changes are present"
      fi
    else
      disable_publish "git status unavailable"
      echo "Apple Reminders export warning: git status unavailable; export will continue." >&2
    fi
  fi
fi

git_remote_url() {
  local url
  if url="$(git remote get-url "$git_remote" 2>/dev/null)" && [[ -n "$url" ]]; then
    print -r -- "$url"
    return 0
  fi
  python3 - "$git_remote" <<'PY'
import re
import sys
from pathlib import Path

remote = sys.argv[1]
config = Path(".git/config")
try:
    text = config.read_text(encoding="utf-8")
except OSError:
    raise SystemExit(1)

pattern = re.compile(
    r'^\s*\[remote\s+"' + re.escape(remote) + r'"\]\s*$'
    r'(?P<body>.*?)(?=^\s*\[|\Z)',
    re.M | re.S,
)
match = pattern.search(text)
if not match:
    raise SystemExit(1)
for line in match.group("body").splitlines():
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() == "url" and value.strip():
        print(value.strip())
        raise SystemExit(0)
raise SystemExit(1)
PY
}

git_can_sync() {
  [[ "$publish_enabled" == "true" ]] &&
    git rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
    git remote get-url "$git_remote" >/dev/null 2>&1
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

record_publish_failure() {
  local message="$1"
  python3 - "$message" data/reminders_export_status.json <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

message = sys.argv[1]
status_path = Path(sys.argv[2])
try:
    status = json.loads(status_path.read_text(encoding="utf-8"))
    if not isinstance(status, dict):
        status = {}
except (OSError, json.JSONDecodeError):
    status = {}

status["last_publish_attempt_at"] = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
status["last_publish_error"] = " ".join(message.split())[:500]
status_path.parent.mkdir(parents=True, exist_ok=True)
status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

publish_failure_status() {
  if [[ ! -f data/reminders_export_status.json ]]; then
    return 0
  fi
  if [[ "$publish_enabled" == "true" ]]; then
    git add data/reminders_export_status.json
    if git diff --cached --quiet; then
      return 0
    fi
    git commit -m "Record Apple Reminders export failure"
    if ! push_with_retry; then
      echo "Apple Reminders export warning: failure-status push failed; retrying via isolated temporary clone." >&2
      publish_export_from_temp_clone
    fi
  else
    echo "Apple Reminders export failure recorded locally; publishing status via isolated temporary clone."
    publish_export_from_temp_clone || true
  fi
}

publish_export_from_temp_clone() {
  local remote_url
  if ! remote_url="$(git_remote_url)" || [[ -z "$remote_url" ]]; then
    echo "Git remote publish unavailable; export remains local."
    record_publish_failure "Git remote publish unavailable."
    return 1
  fi
  publish_dir="$(mktemp -d "${TMPDIR:-/tmp}/daily-cody-reminders-publish.XXXXXX")"
  if ! git clone --quiet --branch "$git_branch" --single-branch "$remote_url" "$publish_dir"; then
    echo "Apple Reminders export warning: temp clone failed; export remains local." >&2
    record_publish_failure "Temporary clone failed while publishing Apple Reminders export."
    return 1
  fi
  mkdir -p "$publish_dir/data"
  cp data/reminders.json data/reminders_export_status.json data/application_wiki_snapshot.json "$publish_dir/data/"
  if ! (
    cd "$publish_dir"
    git config user.name "Daily Cody"
    git config user.email "christian.galler+cody@gmail.com"
    git add data/reminders.json data/reminders_export_status.json data/application_wiki_snapshot.json
    if git diff --cached --quiet; then
      echo "Apple Reminders export unchanged on $git_remote/$git_branch."
    else
      git commit -m "Update Apple Reminders export"
      git push origin "HEAD:$git_branch"
    fi
  ); then
    echo "Apple Reminders export warning: temp clone publish failed; export remains local." >&2
    record_publish_failure "Temporary clone publish failed for Apple Reminders export."
    return 1
  fi
}

if [[ "$publish_enabled" == "true" ]]; then
  if ! sync_with_remote; then
    disable_publish "main worktree sync failed"
    echo "Apple Reminders export warning: main worktree sync failed; export will continue via isolated temporary clone." >&2
  fi
else
  echo "Git publish skipped for Apple Reminders export: $publish_disabled_reason."
fi

rem_timeout_seconds="${REM_EXPORT_TIMEOUT_SECONDS:-120}"
if ! python3 - "$tmp_file" "$rem_timeout_seconds" data/reminders_export_status.json "$rem_command" <<'PY'; then
import datetime as dt
import json
import socket
import subprocess
import sys
from pathlib import Path

tmp_file = sys.argv[1]
timeout_seconds = int(sys.argv[2])
status_path = Path(sys.argv[3])
rem_command = sys.argv[4]


def record_failure(message):
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(status, dict):
            status = {}
    except (OSError, json.JSONDecodeError):
        status = {}

    status["last_attempt_at"] = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    status["last_attempt_host"] = socket.gethostname()
    status["last_error"] = " ".join(str(message).split())[:500]
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


with open(tmp_file, "w", encoding="utf-8") as fh:
    try:
        subprocess.run(
            [rem_command, "export", "--incomplete", "--format", "json"],
            stdout=fh,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=True,
        )
    except subprocess.TimeoutExpired:
        message = f"Apple Reminders export timed out after {timeout_seconds} seconds."
        record_failure(message)
        print(message, file=sys.stderr)
        raise SystemExit(124)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() if exc.stderr else f"rem export failed with exit code {exc.returncode}."
        record_failure(message)
        if exc.stderr:
            print(exc.stderr, end="", file=sys.stderr)
        raise SystemExit(exc.returncode)
PY
  echo "Apple Reminders export failed."
  publish_failure_status
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

if [[ "$publish_enabled" == "true" ]]; then
  git add data/reminders.json data/reminders_export_status.json
fi

if scripts/export_application_wiki_snapshot.sh; then
  if [[ "$publish_enabled" == "true" ]]; then
    git add data/application_wiki_snapshot.json
  fi
else
  echo "Application wiki snapshot failed; continuing with Apple Reminders export." >&2
fi

if [[ "$publish_enabled" != "true" ]]; then
  echo "Apple Reminders export updated local files; publishing via isolated temporary clone."
  if ! publish_export_from_temp_clone; then
    exit 1
  fi
  exit 0
fi

if git diff --cached --quiet; then
  echo "Apple Reminders export unchanged."
else
  git commit -m "Update Apple Reminders export"
  if ! push_with_retry; then
    echo "Apple Reminders export warning: main worktree push failed; retrying via isolated temporary clone." >&2
    publish_export_from_temp_clone
  fi
fi
