#!/usr/bin/env python3
"""Runner preflight: only read tasks; report coverage, never task text or tokens."""
import datetime as dt
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ticktick_tasks
import ticktick_mcp_auth as auth


def main() -> int:
    expected = os.getenv("TICKTICK_MCP_INBOX_PROBE_ID", "")
    if not expected:
        print("TickTick-MCP-Abnahme blockiert: bekannte offene undatierte Inbox-Aufgabe als ID fehlt. "
              "Keine Testaufgabe ohne Freigabe anlegen.", file=sys.stderr)
        return 1
    value = os.getenv("TICKTICK_MCP_AUTH_JSON", "")
    try:
        state = auth.State.from_json(value)
        expected_generation = os.getenv("TICKTICK_MCP_EXPECTED_GENERATION", "")
        if expected_generation and (not expected_generation.isascii() or not expected_generation.isdecimal() or
                                    len(expected_generation) > 10 or state.generation != int(expected_generation)):
            raise auth.AuthError("TickTick-MCP-Abnahme blockiert: Runner hat nicht die erwartete Token-Generation.")
    except auth.AuthError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = ticktick_tasks.read_tasks(value,
                                     dt.datetime.now(ticktick_tasks.BERLIN), required_inbox_task_id=expected)
    if result.warning:
        print(result.warning, file=sys.stderr)
        return 1
    if not result.inbox_probe_verified:
        print("TickTick-MCP-Inbox-ID-Nachweis fehlt; keine Aktivierung.", file=sys.stderr)
        return 1
    print(f"TickTick read successful: {result.project_count} active task lists including Inbox; "
          f"{result.open_task_count} total open tasks, {len(result.tasks)} relevant for the briefing; "
          f"fetched {result.fetched_at}; scope {state.scope}, generation {state.generation}, "
          f"expires_at {state.expires_at}. No task changed, no email sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
