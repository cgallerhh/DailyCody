"""Conservative, exact-reference reconciliation; no mailbox writes or new storage."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import delivery_detection as delivery
import mail_policy


def fingerprint(kind: str, value: str) -> str:
    return "commerce-sha256:" + kind + ":" + hashlib.sha256((kind + ":" + value).encode()).hexdigest()


def item_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(k) or "") for k in (
        "subject", "title", "topic", "body", "snippet", "notes", "change", "relevance", "action"
    ))


def identities(item: dict[str, Any]) -> set[str]:
    text = item_text(item)
    sender = str((item.get("to") if item.get("source") == "gmail_sent" else item.get("from")) or item.get("to") or "")
    order = delivery.delivery_order_key("", sender, text)
    tracking = delivery.extract_delivery_tracking_number("", text)
    result = {fingerprint(k, v) for k, v in (("order", order), ("tracking", tracking)) if v}
    for kind in ("message", "thread"):
        value = item.get(kind + "_id")
        if value:
            result.add(fingerprint(kind, str(value)))
    for source in item.get("sources", []):
        if source.get("kind") == "gmail" and source.get("id"):
            result.add(fingerprint("message", source["id"]))
    return result


def completion_states(message: dict[str, Any]) -> set[str]:
    """Only affirmative completed financial events; announcements never close."""
    sender = delivery.message_sender(message)
    subject = str(message.get("subject") or "")
    body = mail_policy.authored_text(str(message.get("body") or message.get("snippet") or ""))
    text = mail_policy.normalized(subject + " " + body)
    if not delivery.extract_delivery_order_number(subject, body):
        return set()
    if re.search(r"\b(tesla|leasing|experteer|reisekosten|arbeitsagentur|bundesagentur)\b", text + " " + sender.lower()):
        return set()
    domains = [a.split("@", 1)[1] for a in mail_policy.addresses(sender)]
    payment_source = any(d == root or d.endswith("." + root) for d in domains for root in ("riverty.com", "paypal.com", "klarna.com"))
    if not (payment_source or delivery.is_transactional_delivery_source(sender, subject, "", body)):
        return set()
    if mail_policy.excluded_person(sender):
        return set()
    # Be conservative for partial amounts, questions, reversals and future promises.
    if re.search(r"\b(nicht|noch|keine?|teilweise|teilbetrag|angekundigt|veranlasst|geplant|demnachst|wird|werden|storniert|rucklastschrift)\b|\?", text):
        return set()
    states = set()
    if re.search(r"\b(?:zahlung\s+(?:ist\s+)?eingegangen|zahlung\s+(?:wurde|ist)\s+(?:erfolgreich\s+)?(?:erhalten|verbucht)|betrag\s+(?:wurde|ist)\s+(?:vollstandig\s+)?bezahlt|payment\s+(?:received|completed))\b", text):
        states.add("paid")
    # An item credit must not settle an entire order's refund follow-up.
    if re.search(r"\b(?:(?:betrag|bestellung)\s+(?:wurde|ist)\s+vollstandig\s+(?:erstattet|zuruckgezahlt)|ruckerstattung\s+(?:ist\s+)?vollstandig\s+(?:abgeschlossen|erfolgt)|refund\s+(?:completed|received)\s+in\s+full)\b", text):
        states.add("refunded")
    return states


def load_confirmations(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("commerce_completed", [])
    if not isinstance(entries, list):
        raise ValueError("Commerce completion state is invalid")
    return entries


def completion_events(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for message in messages:
        states = completion_states(message)
        if states:
            result.append({"identities": sorted(identities(message)), "states": sorted(states),
                           "through_ms": delivery.message_sort_key(message), "source": "mail"})
    return result


def is_completed(item: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    ids = identities(item)
    if not ids:
        return False
    text = mail_policy.normalized(item_text(item))
    if re.search(r"\b(tesla|leasing|experteer|reisekosten|arbeitsagentur|bundesagentur)\b", text):
        return False
    required = "refunded" if re.search(r"erstatt|ruckzahl|retour|rucksend|ruckgabe|refund", text) else (
        "paid" if re.search(r"zahl|bezahlt|rechnung|payment", text) else "delivered")
    when = delivery.message_sort_key(item)
    if not when and item.get("observed_at"):
        when = delivery.message_sort_key({"timestamp": item["observed_at"]})
    for event in events:
        if required not in event.get("states", []) or not ids.intersection(event.get("identities", [])):
            continue
        # Subsequent disputes/reopened cases must survive old confirmations.
        if when and when > event.get("through_ms", 0):
            continue
        return True
    return False


def filter_open(items: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not is_completed(item, events)]
