#!/usr/bin/env python3
"""Runner preflight: only read tasks; report coverage, never task text or tokens."""
import datetime as dt
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ticktick_tasks


def main() -> int:
    result = ticktick_tasks.read_tasks(os.getenv("TICKTICK_ACCESS_TOKEN", ""),
                                     dt.datetime.now(ticktick_tasks.BERLIN))
    if result.warning:
        print(result.warning, file=sys.stderr)
        return 1
    print(f"TickTick read successful: {result.project_count} active task lists including Inbox; "
          f"{result.open_task_count} total open tasks, {len(result.tasks)} relevant for the briefing; "
          f"fetched {result.fetched_at}. No task changed, no email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
