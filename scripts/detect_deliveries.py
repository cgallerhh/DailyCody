#!/usr/bin/env python3
"""Detect open deliveries from a JSON mail export.

Input can be either a list of message dictionaries or an object with:
{
  "messages": [...],
  "completed_topics": [...]
}
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

import delivery_detection  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect open deliveries from JSON mail messages.")
    parser.add_argument("input", nargs="?", help="JSON file. Reads stdin when omitted or '-'.")
    parser.add_argument("--now", help="Reference datetime, ISO-8601. Defaults to current local time.")
    parser.add_argument("--completed", action="append", default=[], help="Completed topic phrase. Repeatable.")
    parser.add_argument("--own-email", action="append", default=[], help="Own email address to ignore. Repeatable.")
    parser.add_argument("--sender-email", help="Cody sender address; used with --recipient-email for plus-alias expansion.")
    parser.add_argument("--recipient-email", help="Recipient address; used with --sender-email for plus-alias expansion.")
    args = parser.parse_args()

    payload = load_json_payload(args.input)
    messages, completed_topics = normalize_payload(payload)
    completed_topics.extend(args.completed)
    own_addresses = set()
    for value in args.own_email:
        own_addresses.update(delivery_detection.extract_email_addresses(value))
    if args.sender_email or args.recipient_email:
        own_addresses.update(
            delivery_detection.delivery_completion_addresses(
                args.sender_email or "",
                args.recipient_email or "",
            )
        )

    deliveries = delivery_detection.detect_open_deliveries(
        messages,
        parse_now(args.now),
        completed_topics=completed_topics,
        own_addresses=own_addresses or None,
    )
    print(json.dumps(deliveries, ensure_ascii=False, indent=2))


def load_json_payload(path: str | None) -> Any:
    if not path or path == "-":
        return json.load(sys.stdin)
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def normalize_payload(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)], []
    if not isinstance(payload, dict):
        raise SystemExit("Input must be a JSON list or an object with a messages list.")
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        raise SystemExit("Input object must contain a messages list.")
    completed = payload.get("completed_topics", payload.get("completed", []))
    if not isinstance(completed, list):
        completed = []
    return [item for item in messages if isinstance(item, dict)], [str(item) for item in completed if str(item).strip()]


def parse_now(value: str | None) -> dt.datetime:
    if not value:
        return dt.datetime.now().astimezone()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--now must be ISO-8601, got: {value}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed


if __name__ == "__main__":
    main()
