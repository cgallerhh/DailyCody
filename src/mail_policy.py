"""Shared intake and conversation-state rules for personal briefing mail."""

from __future__ import annotations

import email.utils
import html
import re
import unicodedata
from typing import Any


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKD", html.unescape(value)).encode("ascii", "ignore").decode().lower()


def addresses(value: str) -> set[str]:
    return {address.lower() for _, address in email.utils.getaddresses([value]) if "@" in address}


def excluded_person(*values: str) -> bool:
    for value in values:
        text = normalized(value)
        if re.search(r"\beveline\b", text) or any("eveline" in a.split("@")[0] for a in addresses(value)):
            return True
    return False


def authored_text(value: str) -> str:
    value = html.unescape(str(value or ""))
    boundary = re.search(
        r"(?:^|\n|\s)(?:Von:|From:|Am\s+[^\n]{0,180}\bschrieb(?:\s+[^:]{0,100})?:|"
        r"On\s+[^\n]{0,180}\bwrote:|[-_]{3,}\s*(?:Original|Urspr))",
        value, flags=re.I,
    )
    if boundary:
        value = value[:boundary.start()]
    kept = []
    for line in value.splitlines():
        if line.lstrip().startswith(">"):
            break
        if re.match(r"\s*(?:viele(?:\s+liebe)?\s+gr[\u00fc u]?[\u00df s]*e|mit freundlichen|best regards|--\s*$)", line, re.I):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def personal_message(message: dict[str, Any]) -> bool:
    sender = str(message.get("from") or message.get("from_") or "")
    if not sender or excluded_person(sender, str(message.get("to") or "")):
        return False
    labels = set(message.get("labels") or message.get("labelIds") or [])
    if labels.intersection({"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS", "SPAM", "TRASH", "DRAFT"}):
        return False
    headers = {str(k).lower(): str(v).lower() for k, v in (message.get("headers") or {}).items()}
    if headers.get("list-id") or headers.get("list-unsubscribe"):
        return False
    if headers.get("precedence") in {"bulk", "list", "junk"}:
        return False
    if headers.get("auto-submitted", "no") != "no":
        return False
    for address in addresses(sender):
        local, domain = address.split("@", 1)
        if any(part in local for part in ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "newsletter", "notifications")):
            return False
        if any(part in domain for part in ("newsletter", "fashionnews.", "mailchimp.", "substack.")):
            return False
    return True


def closed_reply(value: str) -> bool:
    text = normalized(authored_text(value))
    return any(marker in text for marker in (
        "fur einen anderen wagen entschieden", "fur ein anderes auto entschieden",
        "kein interesse mehr", "nicht weiter verfolgen", "hat sich erledigt",
        "vorgang ist abgeschlossen", "vorgang ist erledigt", "anfrage zuruckziehen",
    ))


def requests_response(subject: str, value: str) -> bool:
    if closed_reply(value):
        return False
    text = normalized(authored_text(value))
    text = re.sub(r"https?://\S+", "", text)
    if "?" in text:
        return True
    return bool(re.search(
        r"\b(?:bitte\s+(?:um\s+)?(?:eine\s+)?(?:kurze\s+)?(?:ruckmeldung|antwort|bestatig|pruf|meld|teil|gib|sag)|"
        r"ich\s+(?:warte|bitte)\s+(?:auf|um)|(?:kannst|konntest|konnten)\s+(?:du|sie)|"
        r"lass(?:en)?\s+(?:mich|sie mich)\s+wissen)", text,
    ))


def meaningful_update(subject: str, value: str) -> bool:
    text = normalized(f"{subject} {authored_text(value)}")
    return requests_response(subject, value) or any(marker in text for marker in (
        "angebot", "vertragsentwurf", "kundigung", "einzug", "erstattung",
        "ruckgabe", "inspektion", "arbeitszeit", "arbeitsbescheinigung",
        "gehaltsabrechnung", "interview", "bewerbung", "frist", "bestatigter termin",
    ))


def excerpt(value: str, limit: int = 420) -> str:
    lines = [line.strip() for line in authored_text(value).splitlines() if line.strip()]
    while lines and re.match(r"^(?:hallo|guten (?:morgen|tag|abend)|sehr geehrte|lieber|liebe)\b", lines[0], re.I) and len(lines) > 1:
        lines.pop(0)
    text = " ".join(" ".join(lines).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."
