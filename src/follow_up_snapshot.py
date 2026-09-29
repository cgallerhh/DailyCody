"""Validate and read the monitor's source-backed handoff to Daily Cody."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


SOURCE_KINDS = {"gmail", "google_calendar", "outlook_mail", "outlook_calendar"}
SOURCE_HOSTS = {
    "gmail": {"mail.google.com"},
    "google_calendar": {"calendar.google.com", "www.google.com"},
    "outlook_mail": {"outlook.live.com", "outlook.office.com", "outlook.office365.com"},
    "outlook_calendar": {"outlook.live.com", "outlook.office.com", "outlook.office365.com"},
}


def parse_timestamp(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("Zeitstempel fehlt")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Zeitstempel ist ungueltig") from exc
    if parsed.tzinfo is None:
        raise ValueError("Zeitstempel braucht eine Zeitzone")
    return parsed


def validate_snapshot(payload: Any, now: dt.datetime) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Snapshot-Schema ist ungueltig")
    if payload.get("status") != "complete":
        raise ValueError("Quellenpruefung ist nicht vollstaendig")
    checked_from = parse_timestamp(payload.get("checked_from"))
    checked_until = parse_timestamp(payload.get("checked_until"))
    generated_at = parse_timestamp(payload.get("generated_at"))
    if not checked_from <= checked_until <= generated_at <= now + dt.timedelta(minutes=5):
        raise ValueError("Pruefzeitraum ist ungueltig oder liegt in der Zukunft")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or any(coverage.get(key) != "ok" for key in SOURCE_KINDS):
        raise ValueError("Nicht alle Mail- und Kalenderquellen wurden geprueft")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > 5:
        raise ValueError("Snapshot braucht eine Liste mit maximal fuenf Punkten")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Follow-up ist ungueltig")
        for field in ("id", "topic", "change", "relevance", "action"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise ValueError(f"Follow-up-Feld {field} ist ungueltig")
        if item["id"] in seen:
            raise ValueError("Follow-up-IDs sind nicht eindeutig")
        seen.add(item["id"])
        observed_at = parse_timestamp(item.get("observed_at"))
        expires_at = parse_timestamp(item.get("expires_at"))
        if observed_at > checked_until or expires_at <= observed_at:
            raise ValueError("Follow-up-Zeitraum ist ungueltig")
        sources = item.get("sources")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 5:
            raise ValueError("Follow-up braucht nachpruefbare Quellen")
        for source in sources:
            if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
                raise ValueError("Follow-up-Quelle ist ungueltig")
            if not isinstance(source.get("id"), str) or not source["id"].strip():
                raise ValueError("Quellen-ID fehlt")
            url = urlparse(str(source.get("url") or ""))
            if (
                url.scheme != "https" or url.hostname not in SOURCE_HOSTS[source["kind"]]
                or url.username is not None or url.password is not None
            ):
                raise ValueError("Quellenlink ist ungueltig")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 40_000:
        raise ValueError("Snapshot ist zu gross fuer die sichere Uebergabe")
    return copy.deepcopy(payload)


def latest_scheduled_check(now: dt.datetime) -> dt.datetime:
    expected = now.replace(hour=5, minute=0, second=0, microsecond=0)
    if expected > now:
        expected -= dt.timedelta(days=1)
    while expected.weekday() >= 5:
        expected -= dt.timedelta(days=1)
    return expected


def read_snapshot(
    now: dt.datetime, encoded: str | None = None, path: Path | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not encoded and path is not None:
            encoded = path.read_text(encoding="utf-8")
        if not encoded:
            return None, "Follow-Up-Monitor: aktuelle Ergebnisse sind nicht verf\u00fcgbar."
        payload = validate_snapshot(json.loads(encoded), now)
    except (OSError, UnicodeDecodeError, ValueError, TypeError) as exc:
        return None, f"Follow-Up-Monitor: Ergebnisse sind nicht nutzbar ({exc})."
    checked_until = parse_timestamp(payload["checked_until"]).astimezone(now.tzinfo)
    expected = latest_scheduled_check(now)
    if checked_until < expected - dt.timedelta(minutes=15):
        return None, (
            f"Follow-Up-Monitor: Stand {checked_until:%d.%m.%Y %H:%M} ist veraltet; "
            "aktuelle Erkenntnisse fehlen."
        )
    payload["items"] = [
        item for item in payload["items"] if parse_timestamp(item["expires_at"]) > now
    ]
    return payload, None


def format_items(snapshot: dict[str, Any] | None, warning: str | None, now: dt.datetime) -> list[str]:
    if warning:
        return [f"- Hinweis: {warning}"]
    if snapshot is None:
        return []
    checked_until = parse_timestamp(snapshot["checked_until"]).astimezone(now.tzinfo)
    lines = [f"- Gepr\u00fcft: {checked_until:%d.%m.%Y, %H:%M} Uhr."]
    for item in snapshot["items"]:
        text = {key: " ".join(item[key].split()) for key in ("topic", "change", "relevance", "action")}
        source = item["sources"][0]["url"]
        lines.append(
            f"- **{text['topic']}:** {text['change']} {text['relevance']} "
            f"Aktion: {text['action']} [Quelle]({source})"
        )
    if not snapshot["items"]:
        lines.append("- Keine aktuell relevanten Follow-ups im gepr\u00fcften Zeitraum.")
    return lines
