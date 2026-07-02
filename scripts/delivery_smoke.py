#!/usr/bin/env python3
"""Diagnostic run for Daily Cody delivery detection in GitHub Actions."""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

import daily_cody  # noqa: E402
import delivery_detection  # noqa: E402


def headers_from_message(message: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def fetch_full_message(token: str, message_id: str) -> dict[str, Any]:
    return daily_cody.request_json(f"{daily_cody.GMAIL_API}/messages/{message_id}?format=full", token=token)


def normalized_message(full_message: dict[str, Any]) -> dict[str, Any]:
    headers = headers_from_message(full_message)
    return {
        "from": headers.get("from", ""),
        "subject": headers.get("subject", "(ohne Betreff)"),
        "date": headers.get("date", ""),
        "snippet": full_message.get("snippet", ""),
        "body": daily_cody.extract_message_text(full_message.get("payload", {})),
        "thread_id": full_message.get("threadId", ""),
        "sort_key": int(full_message.get("internalDate", "0")),
    }


def query_gmail(token: str, query_text: str, max_results: int = 20) -> dict[str, Any]:
    query = urllib.parse.urlencode({"q": query_text, "maxResults": str(max_results)})
    return daily_cody.request_json(f"{daily_cody.GMAIL_API}/messages?{query}", token=token)


def candidate_debug(message: dict[str, Any], now: dt.datetime) -> dict[str, Any]:
    subject = str(message.get("subject") or "")
    snippet = str(message.get("snippet") or "")
    body = str(message.get("body") or "")
    candidate = delivery_detection.delivery_candidate_from_message(message, now, completed_topics=[])
    return {
        "from": message.get("from", "")[:120],
        "subject": subject[:160],
        "looks_like_delivery": delivery_detection.looks_like_delivery(subject, snippet, body),
        "is_noise": delivery_detection.is_delivery_noise(subject, snippet, body),
        "status": delivery_detection.classify_delivery_status(subject, snippet, body),
        "candidate_subject": candidate.get("subject") if candidate else None,
        "candidate_status": candidate.get("status") if candidate else None,
        "eta": candidate.get("eta_end_date") if candidate else None,
    }


def main() -> int:
    config = daily_cody.load_config()
    now = dt.datetime.now(ZoneInfo(config.timezone))
    token = daily_cody.refresh_google_token(config)
    profile = daily_cody.request_json(f"{daily_cody.GMAIL_API}/profile", token=token)
    print(
        "Gmail profile: "
        f"email={profile.get('emailAddress')} messagesTotal={profile.get('messagesTotal')} "
        f"threadsTotal={profile.get('threadsTotal')}",
        file=sys.stderr,
    )

    queries = delivery_detection.delivery_search_queries()
    seen: set[str] = set()
    refs: list[dict[str, Any]] = []
    query_summaries: list[dict[str, Any]] = []
    for index, query_text in enumerate(queries):
        payload = query_gmail(token, query_text)
        items = payload.get("messages", [])
        query_summaries.append(
            {
                "index": index,
                "estimate": payload.get("resultSizeEstimate"),
                "returned": len(items),
                "query": query_text,
            }
        )
        for item in items:
            message_id = str(item.get("id") or "")
            if message_id and message_id not in seen:
                seen.add(message_id)
                refs.append(item)

    matched_queries = [entry for entry in query_summaries if entry["returned"] or entry["estimate"]]
    print(
        f"Delivery query summary: queries={len(queries)} matched={len(matched_queries)} unique_refs={len(refs)}",
        file=sys.stderr,
    )
    for entry in matched_queries[:20]:
        print(
            "Delivery query matched: "
            f"idx={entry['index']} returned={entry['returned']} estimate={entry['estimate']} "
            f"q={entry['query'][:220]}",
            file=sys.stderr,
        )
    if not matched_queries:
        for entry in query_summaries[:8]:
            print(
                "Delivery query empty: "
                f"idx={entry['index']} returned={entry['returned']} estimate={entry['estimate']} "
                f"q={entry['query'][:220]}",
                file=sys.stderr,
            )

    messages = []
    for ref in refs[:80]:
        full = fetch_full_message(token, str(ref.get("id")))
        message = normalized_message(full)
        if delivery_detection.is_own_delivery_sender(message.get("from", ""), config.sender, config.recipient):
            continue
        messages.append(message)

    print(f"Delivery fetched messages: {len(messages)}", file=sys.stderr)
    for message in messages[:20]:
        print(
            "Delivery fetched subject: "
            f"from={message.get('from', '')[:100]} subject={message.get('subject', '')[:160]}",
            file=sys.stderr,
        )

    completed_topics = daily_cody.load_completed_delivery_topics() + daily_cody.list_completed_delivery_mail_topics(
        token, config.sender, config.recipient
    )
    print(f"Completed delivery topics: count={len(completed_topics)} sample={completed_topics[:12]}", file=sys.stderr)

    print("Candidate debug sample:", file=sys.stderr)
    for item in [candidate_debug(message, now) for message in messages[:20]]:
        print(json.dumps(item, ensure_ascii=False), file=sys.stderr)

    deliveries_without_completed = delivery_detection.detect_open_deliveries(messages, now, completed_topics=[])
    deliveries_with_completed = delivery_detection.detect_open_deliveries(
        messages,
        now,
        completed_topics=completed_topics,
    )
    print(
        "Delivery result without completed topics: "
        + json.dumps(deliveries_without_completed, ensure_ascii=False),
        file=sys.stderr,
    )
    print(
        "Delivery result with completed topics: "
        + json.dumps(deliveries_with_completed, ensure_ascii=False),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
