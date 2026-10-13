"""Exact reply relationships; never infer settlement from a received answer."""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from commerce_reconciliation import fingerprint
import mail_policy


def message_ids(value: str) -> set[str]:
    return set(re.findall(r"<[^<>\s]+>", value))


def substantive(message: dict[str, Any]) -> bool:
    if not mail_policy.personal_message(message):
        return False
    body = mail_policy.authored_text(str(message.get("body") or message.get("snippet") or ""))
    if not body.strip():
        return False
    subject = mail_policy.normalized(str(message.get("subject") or ""))
    text = mail_policy.normalized(body)
    if re.search(r"automatische (?:antwort|eingangsbestatigung)|automatic reply|auto.?reply|out of office", subject):
        return False
    return not re.search(
        r"(?:wir haben (?:ihre|deine) (?:nachricht|e-?mail|anfrage) erhalten|"
        r"(?:ihre|deine) (?:nachricht|e-?mail|anfrage) (?:ist (?:bei uns )?(?:eingegangen|angekommen)|wurde .*registriert)|"
        r"we have received your (?:message|email|request))", subject + " " + text,
    )


def load_links(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    links = json.loads(path.read_text()).get("verified_reply_links", [])
    if not isinstance(links, list) or any(
        not isinstance(link, dict) or not all(
            re.fullmatch(r"commerce-sha256:message:[0-9a-f]{64}", str(link.get(key, "")))
            for key in ("request", "reply")
        ) for link in links
    ):
        raise ValueError("Verified reply links are invalid")
    return links


def has_reply(request: dict[str, Any], replies: list[dict[str, Any]], own: set[str], links: list[dict[str, str]]) -> bool:
    original_ids = message_ids(request.get("headers", {}).get("message-id", ""))
    request_key = fingerprint("message", request["message_id"])
    verified = {link["reply"] for link in links if link["request"] == request_key}
    for reply in replies:
        if reply.get("sort_key", 0) <= request["sort_key"] or mail_policy.addresses(reply.get("from", "")) & own:
            continue
        if not substantive(reply):
            continue
        headers = reply.get("headers", {})
        parents = message_ids(headers.get("in-reply-to", "")) | message_ids(headers.get("references", ""))
        if original_ids & parents or fingerprint("message", reply["message_id"]) in verified:
            return True
    return False
