"""Short source-based mail cards, without quoting or truncating message bodies."""
from __future__ import annotations

import email.utils
import re
from typing import Any

import mail_policy


def deadline(value: str) -> str:
    # Only explicit affirmative date deadlines, never a bare date or negated one.
    unresolved = False
    for clause in re.split(r"[!?;\n]|(?<!\d)\.(?=\s|$)", value):
        if re.search(r"\b(nicht|keine?|entfällt|entfallt|aufgehoben)\b", clause, re.I):
            continue
        match = re.search(r"\b(?:bis(?:\s+spätestens)?|spätestens(?:\s+bis)?)\s+(?:zum\s+)?(\d{1,2}\.\d{1,2}\.(?:\d{4})?(?:\s+(?:um\s+)?\d{1,2}:\d{2}(?:\s*Uhr)?)?)", clause, re.I)
        if match:
            return match[1].strip()
        if re.search(r"\b(?:spätestens|frist|bis\s+(?:(?:zum|diesen|kommenden)\s+)?(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|morgen|übermorgen|\d))\b", clause, re.I):
            unresolved = True
    return "Angabe in der Mail prüfen" if unresolved else ""


def core(subject: str, body: str) -> str:
    text = mail_policy.normalized(body)
    if "rechnungsbetrag" in text and "verringert" in text:
        return "Rechnungskorrektur mitgeteilt; eine Auszahlung ist damit nicht belegt."
    if "lastschrift" in text and re.search(r"demnachst|geplant|werden.*abbuchen", text):
        return "Lastschrift angekündigt; noch kein Nachweis der Abbuchung."
    if "ruckversand" in text and "kosten" in text:
        return "Rückgabe: Bedingungen für den Rückversand mitgeteilt."
    if re.search(r"\b(nicht|noch nicht|keine)\b", text):
        # Keep uncertain/negated content uncertain; never cut before the negation.
        return "Einschränkung zum Vorgang mitgeteilt; Details prüfen."
    if "vertragsentwurf" in text and re.search(r"\b(folgt|kommt|zusenden|zukommen)\b", text):
        return "Vertragsentwurf angekündigt; noch keine Zusage."
    if re.search(r"rucksendecode|rucksendeetikett|paketaufkleber", text):
        return "Rücksendeunterlagen angefragt."
    if "kundigung" in text and "bestatigt" in text:
        return "Kündigung bestätigt."
    if "termin" in text and "bestatigt" in text:
        return "Termin bestätigt."
    if "erstattung" in text or "ruckzahlung" in text:
        return "Rückmeldung zur Erstattung; Zahlungsstatus in der Quelle prüfen."
    if mail_policy.requests_response(subject, body):
        return "Persönliche Rückfrage eingegangen."
    return "Neue Information zum Vorgang."


def format_item(item: dict[str, Any], *, answered: bool = False, waiting: bool = False, reply_open: bool = False) -> str:
    body = mail_policy.authored_text(str(item.get("body") or item.get("snippet") or ""))
    subject = " ".join(str(item.get("subject") or "Nachricht").split())
    # An oversized subject is also not an excuse to paste a raw text block.
    topic = re.sub(r"^(?:(?:re|aw|fwd|wg):\s*)+", "", subject, flags=re.I)
    if len(topic) > 100:
        topic = "Nachricht zum offenen Vorgang"
    counterpart = str(item.get("to") if waiting else item.get("from") or "")
    name, address = email.utils.parseaddr(counterpart)
    sender = name.strip('"') or address or "Absender siehe Mail"
    if len(sender) > 70:
        sender = address or "Absender siehe Mail"
    point = "Rückmeldung auf die eigene Anfrage ausstehend." if waiting else core(subject, body)
    action = "Antwort bereits gesendet; keine erneute Antwortaufgabe." if answered else (
        "Rückmeldung abwarten." if waiting else (
            "Antwort offen; Rückfrage prüfen." if reply_open or (mail_policy.personal_message(item) and mail_policy.requests_response(subject, body)) else "Information prüfen."
        ))
    due = deadline(body)
    source = item.get("source_url")
    if not source and item.get("message_id"):
        source = f"https://mail.google.com/mail/u/0/#all/{item['message_id']}"
    link = f" [Mail]({source})" if source else ""
    return f"- **{topic}** — {sender}: {point} Aktion: {action}" + (f" Frist: {due}." if due else "") + link
