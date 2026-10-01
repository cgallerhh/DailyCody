"""Shared intake and conversation-state rules for personal briefing mail."""

from __future__ import annotations

import email.utils
import html
import re
import unicodedata
from typing import Any


# These are notification roles, not merchant domains or general support inboxes.
# Ignore punctuation and +tags so the same role works across common senders.
AUTOMATED_MAILBOXES = {
    "versandbestatigung", "versandbestaetigung",
    "bestellbestatigung", "bestellbestaetigung",
    "orderconfirmation", "shippingconfirmation", "shipmentconfirmation",
    "shipmenttracking", "deliverynotification", "deliveryconfirmation",
}


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
        mailbox = re.sub(r"[^a-z0-9]", "", normalized(local.split("+", 1)[0]))
        if mailbox in AUTOMATED_MAILBOXES:
            return False
    body = normalized(authored_text(str(message.get("body") or message.get("snippet") or "")))
    if re.search(
        r"\b(?:diese\s+(?:e-?mail|nachricht)\s+(?:wurde|ist)\s+automatisch\s+(?:generiert|erstellt)|"
        r"this\s+(?:e-?mail|message)\s+(?:was|is)\s+automatically\s+generated)\b",
        body,
    ):
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
    for sentence in re.findall(r"[^.!?]+[.!?]*", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        # Only the questions explicitly framed as discussion topics or private
        # reflections are excluded. A separate real ask in the same mail survives.
        if "?" in sentence and not discussion_or_self_question(sentence):
            return True
        if re.search(
            r"\b(?:bitte\s+(?:um\s+)?(?:eine\s+)?(?:kurze\s+)?(?:ruckmeldung|antwort|bestatig|pruf|meld|teil|gib|sag)|"
            r"ich\s+(?:warte|bitte)\s+(?:auf|um)|"
            r"lass(?:en)?\s+(?:mich|sie mich)\s+wissen)", sentence,
        ):
            return True
        # Polite requests can follow a condition or preamble. An explicit
        # "bitte" or direct benefit to the sender distinguishes them from
        # "Wenn ..., kannst du ... zuruecksenden" and other permissions.
        modal = re.search(r"\b(?:kannst|konntest|konnen|konnten)\s+(?:du|sie)\b(.*)", sentence, re.S)
        if modal:
            rest = modal.group(1)
            if re.search(r"\bbitte\b", rest) or (
                re.match(r"\s+(?:mir|uns)\b", rest)
                and not re.search(r"\b(?:gerne?|jederzeit|bei bedarf)\b", rest)
            ):
                return True
        # Otherwise, without '?', require an interrogative opening.
        opening = re.sub(r"^(?:hallo|hi|guten tag|liebe[r]?)\s+[^,\n]+[,\n]\s*", "", sentence)
        if re.match(r"(?:kannst|konntest|konnen|konnten)\s+(?:du|sie)\b", opening):
            if not re.search(r"\b(?:gerne?|jederzeit|bei bedarf)\b", opening):
                return True
    return False


def discussion_or_self_question(sentence: str) -> bool:
    """Recognize narrow, explicit non-reply question contexts in normalized text."""
    introduction, colon, question = sentence.partition(":")
    # Questions addressed to the recipient remain asks even after an agenda
    # introduction; this also covers non-modal and wh-question word order.
    if colon and re.search(r"\b(?:du|dir|dich|dein\w*|sie|ihnen|ihr|euch|euer\w*)\b", question):
        return False
    if colon and (
        re.fullmatch(r"(?:unsere\s+)?(?:agenda|leitfragen|diskussionsfragen)", introduction.strip())
        or (
            re.search(r"\b(?:termin|gesprach|meeting|workshop)\b", introduction)
            and re.search(r"\b(?:besprechen|klaren|abgleichen|diskutieren)\b", introduction)
            and not re.match(r"(?:wann|wie|was|wer|wo|welch\w*)\b", introduction)
        )
    ):
        return True
    if re.search(r"\b(?:ich frage mich|frage an mich selbst|notiz an mich)\b", sentence):
        return not re.search(r"\b(?:du|dir|dich|dein\w*|sie|ihnen|ihr|euch|euer\w*)\b", sentence)
    return False


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
