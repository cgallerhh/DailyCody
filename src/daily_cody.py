#!/usr/bin/env python3
"""Daily Cody: send a personal morning briefing from GitHub Actions."""

from __future__ import annotations

import base64
import datetime as dt
import email.message
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo


GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
OPENAI_API = "https://api.openai.com/v1/chat/completions"


@dataclass
class Config:
    sender: str
    recipient: str
    timezone: str
    calendar_names: list[str]
    weather_latitude: str
    weather_longitude: str
    weather_label: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str
    apple_id: str | None
    apple_app_password: str | None
    openai_api_key: str | None
    openai_model: str
    send_window_hour: int
    force_send: bool
    dry_run: bool


def getenv(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    calendar_names = [
        item.strip()
        for item in getenv("CALENDAR_NAMES", "privat,Geburtstage,A&C,MixedCup2026").split(",")
        if item.strip()
    ]
    return Config(
        sender=getenv("SENDER_EMAIL", "Christian.Galler@gmail.com"),
        recipient=getenv("RECIPIENT_EMAIL", "Christian.Galler@gmail.com"),
        timezone=getenv("TIMEZONE", "Europe/Berlin"),
        calendar_names=calendar_names,
        weather_latitude=getenv("WEATHER_LATITUDE", "53.4439"),
        weather_longitude=getenv("WEATHER_LONGITUDE", "9.9857"),
        weather_label=getenv("WEATHER_LABEL", "21077 Hamburg"),
        google_client_id=getenv("GOOGLE_CLIENT_ID"),
        google_client_secret=getenv("GOOGLE_CLIENT_SECRET"),
        google_refresh_token=getenv("GOOGLE_REFRESH_TOKEN"),
        apple_id=os.getenv("APPLE_ID") or None,
        apple_app_password=os.getenv("APPLE_APP_PASSWORD") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=getenv("OPENAI_MODEL", "gpt-5.5"),
        send_window_hour=int(getenv("SEND_WINDOW_HOUR", "7")),
        force_send=os.getenv("FORCE_SEND", "false").lower() == "true",
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
    )


def request_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def refresh_google_token(config: Config) -> str:
    body = urllib.parse.urlencode(
        {
            "client_id": config.google_client_id,
            "client_secret": config.google_client_secret,
            "refresh_token": config.google_refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload["access_token"]


def get_weather(config: Config) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "latitude": config.weather_latitude,
            "longitude": config.weather_longitude,
            "timezone": config.timezone,
            "current": "temperature_2m,precipitation,rain",
            "hourly": "temperature_2m,precipitation_probability,precipitation,rain",
            "forecast_days": "2",
        }
    )
    data = request_json(f"{OPEN_METEO_API}?{params}")
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    rain_probs = hourly.get("precipitation_probability", [])
    afternoon_probs = [
        rain_probs[index]
        for index, timestamp in enumerate(times)
        if "12:00" <= timestamp[-5:] <= "18:00" and index < len(rain_probs)
    ]
    max_afternoon_rain = max(afternoon_probs) if afternoon_probs else None
    current_temp = data.get("current", {}).get("temperature_2m")
    high = max(temps[:24]) if temps else None
    low = min(temps[:24]) if temps else None
    umbrella = (
        "Nimm lieber einen Schirm mit, es könnte heute Nachmittag regnen."
        if max_afternoon_rain is not None and max_afternoon_rain >= 45
        else "Ein Schirm ist heute wahrscheinlich optional."
    )
    return {
        "label": config.weather_label,
        "current_temp_c": current_temp,
        "high_c": high,
        "low_c": low,
        "afternoon_rain_probability_pct": max_afternoon_rain,
        "umbrella_note": umbrella,
    }


def list_calendar_events(config: Config, token: str, now: dt.datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    calendars = request_json(f"{CALENDAR_API}/users/me/calendarList", token=token).get("items", [])
    wanted = []
    wanted_lower = [name.lower() for name in config.calendar_names]
    for calendar in calendars:
        summary = calendar.get("summary", "")
        if any(name in summary.lower() for name in wanted_lower):
            wanted.append(calendar)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + dt.timedelta(days=1)
    upcoming_end = today_start + dt.timedelta(days=8)
    today: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []

    for calendar in wanted:
        params = urllib.parse.urlencode(
            {
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeMin": today_start.isoformat(),
                "timeMax": upcoming_end.isoformat(),
                "maxResults": "50",
            }
        )
        events = request_json(
            f"{CALENDAR_API}/calendars/{urllib.parse.quote(calendar['id'])}/events?{params}",
            token=token,
        ).get("items", [])
        for event in events:
            normalized = normalize_event(event, calendar.get("summary", "Kalender"))
            event_start = parse_event_start(event, config.timezone)
            if today_start <= event_start < tomorrow_start:
                today.append(normalized)
            else:
                upcoming.append(normalized)
    return today, upcoming[:20]


def parse_event_start(event: dict[str, Any], timezone: str) -> dt.datetime:
    zone = ZoneInfo(timezone)
    raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    if not raw:
        return dt.datetime.now(zone)
    if len(raw) == 10:
        return dt.datetime.fromisoformat(raw).replace(tzinfo=zone)
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(zone)


def normalize_event(event: dict[str, Any], calendar_name: str) -> dict[str, str]:
    start = event.get("start", {})
    end = event.get("end", {})
    start_value = start.get("dateTime") or start.get("date", "")
    end_value = end.get("dateTime") or end.get("date", "")
    return {
        "calendar": calendar_name,
        "summary": event.get("summary", "(ohne Titel)"),
        "start": start_value,
        "end": end_value,
        "location": event.get("location", ""),
        "description": strip_long(event.get("description", ""), 500),
    }


def list_apple_reminders(config: Config, now: dt.datetime) -> list[dict[str, str]]:
    if not config.apple_id or not config.apple_app_password:
        return []
    try:
        principal_url = discover_caldav_property(
            "https://caldav.icloud.com/.well-known/caldav",
            config,
            "current-user-principal",
        )
        home_url = discover_caldav_property(principal_url, config, "calendar-home-set")
        reminder_lists = discover_reminder_collections(home_url, config)
        reminders: list[dict[str, str]] = []
        for collection in reminder_lists:
            reminders.extend(fetch_reminders_from_collection(collection, config))
        return select_relevant_reminders(reminders, now, config.timezone)
    except Exception as exc:
        print(f"Apple Reminders unavailable: {exc}", file=sys.stderr)
        return [
            {
                "list": "Apple Reminders",
                "title": "Apple Reminders konnten nicht gelesen werden",
                "due": "",
                "notes": "Apple-ID/App-Passwort und iCloud-CalDAV-Zugriff prüfen.",
            }
        ]


def caldav_request(
    url: str,
    config: Config,
    *,
    method: str,
    body: str,
    depth: str,
) -> tuple[bytes, str]:
    auth = base64.b64encode(f"{config.apple_id}:{config.apple_app_password}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": depth,
        "User-Agent": "DailyCody/1.0",
    }
    request = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read(), response.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in {301, 302, 307, 308} and exc.headers.get("Location"):
            redirected = urllib.parse.urljoin(url, exc.headers["Location"])
            return caldav_request(redirected, config, method=method, body=body, depth=depth)
        raise


def discover_caldav_property(url: str, config: Config, property_name: str) -> str:
    body = f"""<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:{property_name} />
    <c:{property_name} />
  </d:prop>
</d:propfind>"""
    payload, response_url = caldav_request(url, config, method="PROPFIND", body=body, depth="0")
    root = ET.fromstring(payload)
    href = root.find(f".//{{DAV:}}{property_name}/{{DAV:}}href")
    if href is None:
        href = root.find(f".//{{urn:ietf:params:xml:ns:caldav}}{property_name}/{{DAV:}}href")
    if href is None or not href.text:
        raise RuntimeError(f"CalDAV property not found: {property_name}")
    return urllib.parse.urljoin(response_url, href.text)


def discover_reminder_collections(home_url: str, config: Config) -> list[dict[str, str]]:
    body = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:displayname />
    <d:resourcetype />
    <c:supported-calendar-component-set />
  </d:prop>
</d:propfind>"""
    payload, response_url = caldav_request(home_url, config, method="PROPFIND", body=body, depth="1")
    root = ET.fromstring(payload)
    collections = []
    for response in root.findall("{DAV:}response"):
        href = response.findtext("{DAV:}href")
        if not href:
            continue
        display_name = response.findtext(".//{DAV:}displayname") or "Reminders"
        components = [
            comp.attrib.get("name", "").upper()
            for comp in response.findall(".//{urn:ietf:params:xml:ns:caldav}comp")
        ]
        resource = response.find(".//{DAV:}collection")
        if resource is not None and (not components or "VTODO" in components):
            collections.append({"url": urllib.parse.urljoin(response_url, href), "name": display_name})
    return collections


def fetch_reminders_from_collection(collection: dict[str, str], config: Config) -> list[dict[str, str]]:
    body = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag />
    <c:calendar-data />
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VTODO" />
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
    payload, _ = caldav_request(collection["url"], config, method="REPORT", body=body, depth="1")
    root = ET.fromstring(payload)
    reminders = []
    for calendar_data in root.findall(".//{urn:ietf:params:xml:ns:caldav}calendar-data"):
        if not calendar_data.text:
            continue
        reminders.extend(parse_vtodos(calendar_data.text, collection["name"]))
    return reminders


def parse_vtodos(ics_text: str, list_name: str) -> list[dict[str, str]]:
    lines = unfold_ical_lines(ics_text)
    reminders = []
    current: dict[str, str] | None = None
    for raw_line in lines:
        if raw_line == "BEGIN:VTODO":
            current = {"list": list_name, "title": "(ohne Titel)", "due": "", "notes": "", "status": ""}
            continue
        if raw_line == "END:VTODO":
            if current and current.get("status", "").upper() != "COMPLETED":
                reminders.append(current)
            current = None
            continue
        if current is None or ":" not in raw_line:
            continue
        key_part, value = raw_line.split(":", 1)
        key = key_part.split(";", 1)[0].upper()
        value = unescape_ical_value(value)
        if key == "SUMMARY":
            current["title"] = value
        elif key == "DUE":
            current["due"] = value
        elif key == "DESCRIPTION":
            current["notes"] = strip_long(value, 240)
        elif key == "STATUS":
            current["status"] = value
    return reminders


def unfold_ical_lines(ics_text: str) -> list[str]:
    unfolded: list[str] = []
    for line in ics_text.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        elif line:
            unfolded.append(line)
    return unfolded


def unescape_ical_value(value: str) -> str:
    return (
        value.replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def select_relevant_reminders(
    reminders: list[dict[str, str]],
    now: dt.datetime,
    timezone: str,
) -> list[dict[str, str]]:
    zone = ZoneInfo(timezone)
    upcoming_end = now.replace(hour=23, minute=59, second=59, microsecond=0) + dt.timedelta(days=7)

    def sort_key(reminder: dict[str, str]) -> tuple[int, str, str]:
        due = reminder.get("due", "")
        return (0 if due else 1, due, reminder.get("title", "").lower())

    selected = []
    undated = []
    for reminder in reminders:
        due_raw = reminder.get("due", "")
        if not due_raw:
            undated.append(reminder)
            continue
        due = parse_ical_datetime(due_raw, zone)
        if due <= upcoming_end:
            selected.append(reminder)
    selected.sort(key=sort_key)
    undated.sort(key=sort_key)
    return (selected + undated[:6])[:16]


def parse_ical_datetime(value: str, zone: ZoneInfo) -> dt.datetime:
    try:
        if len(value) == 8:
            return dt.datetime.strptime(value, "%Y%m%d").replace(tzinfo=zone)
        if value.endswith("Z"):
            return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc).astimezone(zone)
        return dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S").replace(tzinfo=zone)
    except ValueError:
        return dt.datetime.max.replace(tzinfo=zone)


def list_recent_mail(token: str) -> list[dict[str, str]]:
    query = urllib.parse.urlencode({"q": "newer_than:2d -category:promotions", "maxResults": "25"})
    messages = request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages", [])
    output = []
    for item in messages[:25]:
        message = request_json(
            f"{GMAIL_API}/messages/{item['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date",
            token=token,
        )
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        output.append(
            {
                "from": headers.get("from", ""),
                "subject": headers.get("subject", "(ohne Betreff)"),
                "date": headers.get("date", ""),
                "snippet": message.get("snippet", ""),
            }
        )
    return output


def list_delivery_mail(token: str) -> list[dict[str, Any]]:
    query_text = (
        "newer_than:60d "
        "{amazon bestellung bestellt versandt versendet lieferung zustellung "
        "sendung tracking paket dhl hermes dpd ups gls proraso comic}"
    )
    query = urllib.parse.urlencode({"q": query_text, "maxResults": "30"})
    messages = request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages", [])
    output = []
    seen = set()
    for item in messages[:30]:
        message = request_json(f"{GMAIL_API}/messages/{item['id']}?format=full", token=token)
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(ohne Betreff)")
        text = extract_message_text(message.get("payload", {}))
        snippet = message.get("snippet", "")
        if not looks_like_delivery(subject, snippet, text):
            continue
        dedupe_key = normalize_delivery_key(subject, headers.get("from", ""))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        links = extract_tracking_links(text)
        output.append(
            {
                "from": headers.get("from", ""),
                "subject": subject,
                "date": headers.get("date", ""),
                "snippet": snippet,
                "tracking_links": links[:3],
                "details": strip_long(text, 900),
            }
        )
        if len(output) >= 10:
            break
    return output


def list_yesterday_open_mail(token: str, now: dt.datetime) -> list[dict[str, str]]:
    yesterday = now.date() - dt.timedelta(days=1)
    today = now.date()
    query_text = (
        f"after:{yesterday:%Y/%m/%d} before:{today:%Y/%m/%d} "
        "-from:me -category:promotions -category:social"
    )
    query = urllib.parse.urlencode({"q": query_text, "maxResults": "25"})
    messages = request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages", [])
    output = []
    for item in messages[:25]:
        message = request_json(
            f"{GMAIL_API}/messages/{item['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=From&metadataHeaders=Date",
            token=token,
        )
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        snippet = message.get("snippet", "")
        subject = headers.get("subject", "(ohne Betreff)")
        if not looks_actionable(subject, snippet):
            continue
        output.append(
            {
                "from": headers.get("from", ""),
                "subject": subject,
                "date": headers.get("date", ""),
                "snippet": snippet,
                "suggested_reply": build_simple_reply(headers.get("from", ""), subject),
            }
        )
        if len(output) >= 8:
            break
    return output


def list_waiting_for_mail(token: str, sender_email: str) -> list[dict[str, str]]:
    query = urllib.parse.urlencode({"q": "in:sent newer_than:7d", "maxResults": "40"})
    messages = request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages", [])
    output = []
    seen_threads = set()
    for item in messages[:40]:
        message = request_json(
            f"{GMAIL_API}/messages/{item['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=To&metadataHeaders=Date",
            token=token,
        )
        thread_id = message.get("threadId", "")
        if thread_id in seen_threads:
            continue
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(ohne Betreff)")
        snippet = message.get("snippet", "")
        if not looks_waiting_for_reply(subject, snippet):
            continue
        sent_at_ms = int(message.get("internalDate", "0"))
        if thread_has_later_external_reply(token, thread_id, sent_at_ms, sender_email):
            continue
        seen_threads.add(thread_id)
        output.append(
            {
                "to": headers.get("to", ""),
                "subject": subject,
                "date": headers.get("date", ""),
                "snippet": snippet,
            }
        )
        if len(output) >= 8:
            break
    return output


def thread_has_later_external_reply(token: str, thread_id: str, sent_at_ms: int, sender_email: str) -> bool:
    if not thread_id:
        return False
    thread = request_json(
        f"{GMAIL_API}/threads/{thread_id}?format=metadata&metadataHeaders=From",
        token=token,
    )
    own = sender_email.lower()
    for message in thread.get("messages", []):
        internal_date = int(message.get("internalDate", "0"))
        if internal_date <= sent_at_ms:
            continue
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "").lower()
        if own not in sender:
            return True
    return False


def extract_message_text(payload: dict[str, Any]) -> str:
    chunks: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if body_data and mime_type in {"text/plain", "text/html"}:
            try:
                decoded = base64.urlsafe_b64decode(body_data + "=" * (-len(body_data) % 4)).decode(
                    "utf-8", errors="replace"
                )
            except ValueError:
                decoded = ""
            if mime_type == "text/html":
                decoded = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.I)
                decoded = re.sub(r"</p\s*>", "\n", decoded, flags=re.I)
                decoded = re.sub(r"<[^>]+>", " ", decoded)
            chunks.append(html.unescape(decoded))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return " ".join(" ".join(chunks).split())


def extract_tracking_links(text: str) -> list[str]:
    links = re.findall(r"https?://[^\s<>\")]+", text)
    wanted = []
    keywords = (
        "track",
        "tracking",
        "sendung",
        "liefer",
        "ship",
        "dhl",
        "hermes",
        "amazon",
        "dpd",
        "ups",
        "gls",
        "post",
    )
    for link in links:
        clean = link.rstrip(".,;:")
        if any(keyword in clean.lower() for keyword in keywords) and clean not in wanted:
            wanted.append(clean)
    return wanted


def looks_like_delivery(subject: str, snippet: str, text: str) -> bool:
    haystack = f"{subject} {snippet} {text[:1200]}".lower()
    markers = (
        "bestellung",
        "bestellt",
        "versandt",
        "versendet",
        "lieferung",
        "zugestellt",
        "zustellung",
        "sendung",
        "tracking",
        "paket",
        "kommt",
        "unterwegs",
        "dhl",
        "hermes",
        "dpd",
        "ups",
        "gls",
        "amazon",
        "proraso",
        "comic",
    )
    return any(marker in haystack for marker in markers)


def normalize_delivery_key(subject: str, sender: str) -> str:
    cleaned = re.sub(r"\b(re|aw|fwd|wg):\s*", "", subject, flags=re.I)
    cleaned = re.sub(r"\d+", "#", cleaned.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    sender_domain = sender.split("@")[-1].lower() if "@" in sender else sender.lower()
    return f"{sender_domain}:{cleaned[:80]}"


def build_simple_reply(sender: str, subject: str) -> str:
    return (
        "Danke dir für die Nachricht. Ich schaue mir das heute an und melde mich "
        "mit einer kurzen Rückmeldung."
    )


def looks_actionable(subject: str, snippet: str) -> bool:
    text = f"{subject} {snippet}".lower()
    markers = (
        "?",
        "bitte",
        "kannst",
        "könntest",
        "koenntest",
        "könnten",
        "koennten",
        "rückmeldung",
        "rueckmeldung",
        "antwort",
        "frage",
        "termin",
        "feedback",
        "freigabe",
        "entscheidung",
        "bestätigen",
        "bestaetigen",
    )
    return any(marker in text for marker in markers)


def looks_waiting_for_reply(subject: str, snippet: str) -> bool:
    text = f"{subject} {snippet}".lower()
    markers = (
        "?",
        "wann",
        "was soll",
        "soll ich",
        "wo soll",
        "wie soll",
        "kann ich",
        "kannst du",
        "bring",
        "mitbringen",
        "kommen",
        "passt",
        "feedback",
        "rückmeldung",
        "rueckmeldung",
        "antwort",
        "bitte gib",
        "bitte sag",
        "kurze info",
    )
    return any(marker in text for marker in markers)


def already_sent_today(config: Config, token: str, subject: str) -> bool:
    if config.force_send:
        return False
    query = urllib.parse.urlencode({"q": f'from:me to:{config.recipient} subject:"{subject}" newer_than:2d'})
    return bool(request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages"))


def build_briefing(
    config: Config,
    now: dt.datetime,
    weather: dict[str, Any],
    today_events: list[dict[str, Any]],
    upcoming_events: list[dict[str, Any]],
    recent_mail: list[dict[str, str]],
    delivery_mail: list[dict[str, Any]],
    open_mail: list[dict[str, str]],
    waiting_for_mail: list[dict[str, str]],
    apple_reminders: list[dict[str, str]],
) -> str:
    context = {
        "date": now.strftime("%A, %d.%m.%Y"),
        "affirmation": daily_affirmation(now),
        "weather": weather,
        "today_events": today_events,
        "upcoming_events": upcoming_events,
        "recent_mail": recent_mail,
        "deliveries": delivery_mail,
        "yesterday_open_mail": open_mail,
        "waiting_for": waiting_for_mail,
        "apple_reminders": apple_reminders,
    }
    if config.openai_api_key:
        try:
            return build_ai_briefing(config, context)
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401, 403, 429}:
                print(
                    f"OpenAI briefing failed with HTTP {exc.code}; using template briefing.",
                    file=sys.stderr,
                )
                return build_template_briefing(context)
            raise
    return build_template_briefing(context)


def build_ai_briefing(config: Config, context: dict[str, Any]) -> str:
    system = (
        "Du bist Cody, Christians ruhiger, praktischer Family Chief of Staff. "
        "Schreibe ein extrem kompaktes deutsches Daily Briefing nach dem Daily-Dover-Muster. "
        "Nutze genau diese Markdown-Struktur: H1-Titel, ein einziger kursiver Satz, dann H2-Abschnitte "
        "'Today', 'Reminders', 'Waiting for...', 'Deliveries', 'Today's to-dos' und 'Approaching'. "
        "Der kursive Satz direkt unter dem Titel muss ein motivierendes Zitat oder eine Affirmation sein. "
        "Nutze, wenn passend, die im Kontext gelieferte affirmation. Keine Aufgaben, Termine oder Erinnerungen in diese Zeile schreiben. "
        "Alles außer Titel und Einleitung muss als kurze Bulletpoints erscheinen. "
        "Kein langer Brief, keine Begrüßung mit Leerzeilen, keine horizontalen Trennstriche, keine Tabellen. "
        "Packe Wetter als 1-2 Bulletpoints unter Today. Unter Deliveries: offene Bestellungen und Lieferungen "
        "aller Händler, zum Beispiel Amazon, Proraso oder Comics, mit Liefertermin und Trackinglink, falls vorhanden. "
        "Unter Reminders: offene Apple Reminders, besonders fällige und überfällige, mit Liste und Datum. "
        "Unter Waiting for...: gesendete Mails der letzten 7 Tage, auf deren Antwort Christian wahrscheinlich wartet. "
        "Unter Today's to-dos: offene Mails vom Vortag, auf die Christian "
        "wahrscheinlich reagieren sollte, jeweils mit einem sehr kurzen Antwortentwurf. "
        "Sei nützlich, konkret, freundlich, und erfinde keine Fakten."
    )
    user = "Nutze diese Daten und schreibe die E-Mail als Markdown:\n\n" + json.dumps(
        context, ensure_ascii=False, indent=2
    )
    body = {
        "model": config.openai_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if not config.openai_model.startswith("gpt-5"):
        body["temperature"] = 0.4
    response = request_json(
        OPENAI_API,
        method="POST",
        token=config.openai_api_key,
        body=body,
    )
    return response["choices"][0]["message"]["content"].strip()


def build_template_briefing(context: dict[str, Any]) -> str:
    weather = context["weather"]
    lines = [
        f"# The Daily Cody — {context['date']}",
        "",
        context["affirmation"],
        "",
        "## Today",
        f"- {weather['label']}: aktuell {weather['current_temp_c']} °C, heute ca. {weather['low_c']} bis {weather['high_c']} °C.",
        f"- Regenwahrscheinlichkeit am Nachmittag: {weather['afternoon_rain_probability_pct']}%. {weather['umbrella_note']}",
    ]
    lines.extend(format_items(context["today_events"], "Heute steht nichts Kritisches im Kalender."))
    lines.extend(["", "## Reminders"])
    lines.extend(format_reminder_items(context["apple_reminders"]))
    lines.extend(["", "## Waiting for..."])
    lines.extend(format_waiting_for_items(context["waiting_for"]))
    lines.extend(["", "## Deliveries"])
    lines.extend(format_delivery_items(context["deliveries"]))
    lines.extend(["", "## Today's to-dos"])
    lines.extend(format_open_mail_items(context["yesterday_open_mail"]))
    lines.extend(["", "## Approaching"])
    lines.extend(format_items(context["upcoming_events"], "Keine nahen Termine gefunden."))
    return "\n".join(lines)


def daily_affirmation(now: dt.datetime) -> str:
    affirmations = [
        "Heute reicht ein klarer nächster Schritt.",
        "Ruhig bleiben, freundlich bleiben, dranbleiben.",
        "Du musst nicht alles gleichzeitig lösen; nur das Nächste gut.",
        "Kleine Fortschritte zählen, besonders an vollen Tagen.",
        "Fokus ist freundlich: weniger anfangen, mehr abschließen.",
        "Heute darf leicht beginnen und trotzdem wirksam werden.",
        "Ein guter Tag entsteht aus wenigen guten Entscheidungen.",
        "Du hast genug Zeit für das, was wirklich wichtig ist.",
        "Erst Überblick, dann Tempo.",
        "Klarheit vor Geschwindigkeit.",
    ]
    return affirmations[now.toordinal() % len(affirmations)]


def format_items(items: list[dict[str, Any]], empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [
        f"- {format_time(item.get('start', ''))} {item.get('summary')} ({item.get('calendar')})".strip()
        for item in items[:12]
    ]


def format_mail_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- Keine auffälligen neuen Mails gefunden."]
    return [f"- Mail prüfen: {item['subject']} — {item['from']}" for item in items[:8]]


def format_delivery_items(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- Keine offenen Liefer- oder Bestellmails gefunden."]
    lines = []
    for item in items[:8]:
        link = f" — [Sendung verfolgen]({item['tracking_links'][0]})" if item.get("tracking_links") else ""
        lines.append(f"- {item['subject']} — {item['snippet']}{link}")
    return lines


def format_reminder_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- Keine fälligen Apple Reminders gefunden."]
    lines = []
    for item in items[:10]:
        due = format_ical_due(item.get("due", ""))
        due_text = f" fällig {due}" if due else ""
        notes = f" — {item['notes']}" if item.get("notes") else ""
        lines.append(f"- {item['title']} ({item['list']}){due_text}{notes}")
    return lines


def format_waiting_for_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- Keine offenen gesendeten Fragen aus den letzten 7 Tagen gefunden."]
    return [
        f"- Antwort offen: {item['subject']} — an {item['to']}; {item['snippet']}"
        for item in items[:8]
    ]


def format_open_mail_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- Keine offenen Vortags-Mails mit klarer Antwortspur gefunden."]
    lines = []
    for item in items[:6]:
        lines.append(
            f"- Offen prüfen: {item['subject']} — {item['from']}. Antwortidee: {item['suggested_reply']}"
        )
    return lines


def format_time(value: str) -> str:
    if not value:
        return ""
    if len(value) == 10:
        return value
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%H:%M")
    except ValueError:
        return value


def format_ical_due(value: str) -> str:
    if not value:
        return ""
    try:
        if len(value) == 8:
            return dt.datetime.strptime(value, "%Y%m%d").strftime("%d.%m.")
        if value.endswith("Z"):
            return dt.datetime.strptime(value, "%Y%m%dT%H%M%SZ").strftime("%d.%m. %H:%M")
        return dt.datetime.strptime(value[:15], "%Y%m%dT%H%M%S").strftime("%d.%m. %H:%M")
    except ValueError:
        return value


def strip_long(value: str, max_len: int) -> str:
    clean = " ".join(value.split())
    return clean[: max_len - 1] + "…" if len(clean) > max_len else clean


def send_email(config: Config, token: str, subject: str, markdown_body: str) -> None:
    plain = markdown_body
    html_body = markdown_to_basic_html(markdown_body)
    message = email.message.EmailMessage()
    message["From"] = config.sender
    message["To"] = config.recipient
    message["Subject"] = subject
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    request_json(f"{GMAIL_API}/messages/send", method="POST", token=token, body={"raw": raw})


def markdown_to_basic_html(markdown_body: str) -> str:
    html_lines = []
    in_list = False
    first_paragraph = True
    section_icons = {
        "Today": "📅",
        "Reminders": "🔔",
        "Waiting for...": "⏳",
        "Deliveries": "📦",
        "Today's to-dos": "✅",
        "Approaching": "🏃",
    }
    for line in markdown_body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"---", "***", "___"}:
            continue
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(
                "<h1 style=\"margin:0 0 10px 0;padding:0 0 8px 0;"
                "border-top:1px solid #9aa0a6;border-bottom:1px solid #c7cdd4;"
                "font-size:18px;line-height:1.25;font-weight:700;color:#202124\">"
                "<span style=\"display:inline-block;width:23px;margin-right:4px;"
                "font-size:15px;vertical-align:1px\">📰</span>"
                f"{render_inline_markdown(line[2:])}</h1>"
            )
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            heading = render_inline_markdown(line[3:])
            icon = section_icons.get(line[3:].strip(), "")
            icon_html = (
                "<span style=\"display:inline-block;width:22px;margin-right:3px;"
                "font-size:14px;font-weight:400;vertical-align:1px\">"
                f"{icon}</span>"
                if icon
                else ""
            )
            html_lines.append(
                "<h2 style=\"margin:14px 0 5px 0;font-size:15px;line-height:1.3;"
                f"font-weight:700;color:#303134\">{icon_html}{heading}</h2>"
            )
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul style=\"margin:0 0 10px 25px;padding:0\">")
                in_list = True
            html_lines.append(
                "<li style=\"margin:3px 0;padding-left:1px;font-size:14px;"
                f"line-height:1.35;color:#2b2f33\">{render_inline_markdown(line[2:])}</li>"
            )
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            style = (
                "margin:8px 0 13px 25px;color:#5f6368;font-size:14px;"
                "line-height:1.35;font-style:italic"
                if first_paragraph
                else "margin:0 0 8px 25px;font-size:14px;line-height:1.35;color:#2b2f33"
            )
            html_lines.append(f"<p style=\"{style}\">{render_inline_markdown(line)}</p>")
            first_paragraph = False
    if in_list:
        html_lines.append("</ul>")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
</head>
<body style="margin:0;padding:0;background:#ffffff;color:#2b2f33;font-family:Arial,Helvetica,sans-serif">
  <div style="max-width:700px;margin:0;padding:18px 20px 22px">
    {"\n".join(html_lines)}
  </div>
</body>
</html>"""


def render_inline_markdown(value: str) -> str:
    escaped = html.escape(value.strip())
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" style="color:#1a73e8;text-decoration:none">\1</a>',
        escaped,
    )
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    return escaped


def main() -> int:
    config = load_config()
    zone = ZoneInfo(config.timezone)
    now = dt.datetime.now(zone)
    if not config.force_send and now.hour != config.send_window_hour:
        print(f"Not send window in {config.timezone}: now={now.isoformat()}")
        return 0

    token = refresh_google_token(config)
    subject = f"The Daily Cody — {now:%Y-%m-%d}"
    if already_sent_today(config, token, subject):
        print(f"Already sent: {subject}")
        return 0

    weather = get_weather(config)
    today_events, upcoming_events = list_calendar_events(config, token, now)
    recent_mail = list_recent_mail(token)
    delivery_mail = list_delivery_mail(token)
    open_mail = list_yesterday_open_mail(token, now)
    waiting_for_mail = list_waiting_for_mail(token, config.sender)
    apple_reminders = list_apple_reminders(config, now)
    briefing = build_briefing(
        config,
        now,
        weather,
        today_events,
        upcoming_events,
        recent_mail,
        delivery_mail,
        open_mail,
        waiting_for_mail,
        apple_reminders,
    )

    if config.dry_run:
        print(briefing)
        return 0

    send_email(config, token, subject, briefing)
    print(f"Sent: {subject} to {config.recipient}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Daily Cody failed: {exc}", file=sys.stderr)
        raise
