#!/usr/bin/env python3
"""Mail-free MCP lifecycle harness. Live refresh needs an approved durable store."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ticktick_mcp
import ticktick_mcp_auth as auth
import ticktick_tasks as tt


def check(store, expected_id: str, *, force_refresh=False, now=None, expected_generation=None) -> dict:
    if not expected_id:
        raise auth.AuthError("Abnahme blockiert: bekannte offene undatierte Inbox-Aufgabe als ID fehlt.")
    token = auth.Lifecycle(store).access_token(now=now, force_refresh=force_refresh)
    state = store.load()
    if expected_generation is not None and state.generation != expected_generation:
        raise auth.AuthError("Abnahme blockiert: Neustart hat nicht den erwarteten dauerhaften Tokenzustand.")
    result = ticktick_mcp.TaskReader(token).read(dt.datetime.now(tt.BERLIN), required_inbox_task_id=expected_id)
    if not result.inbox_probe_verified or result.warning:
        raise auth.AuthError("Abnahme blockiert: bekannte undatierte Inbox-Aufgabe nicht nachgewiesen.")
    return {"scope": state.scope, "resource": state.resource, "expires_at": state.expires_at,
            "generation": state.generation, "refresh_token_present": bool(state.refresh_token),
            "force_refresh_passed": force_refresh, "inbox_id_probe_passed": True,
            "project_count": result.project_count, "open_task_count": result.open_task_count,
            "fetched_at": result.fetched_at, "email_sent": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--expected-generation", type=int)
    args = parser.parse_args()
    # A snapshot secret is deliberately not presented as a writable token store.
    store = auth.EnvironmentStore(os.getenv("TICKTICK_MCP_AUTH_JSON", ""))
    try:
        receipt = check(store, os.getenv("TICKTICK_MCP_INBOX_PROBE_ID", ""),
                        force_refresh=args.force_refresh, expected_generation=args.expected_generation)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except (auth.AuthError, ticktick_mcp.MCPError, tt.TickTickError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("TickTick-MCP-Abnahme fehlgeschlagen; keine geheimen Details ausgegeben.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
