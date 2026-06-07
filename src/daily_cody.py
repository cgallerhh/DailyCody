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
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=getenv("OPENAI_MODEL", "gpt-4.1-mini"),
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
) -> str:
    context = {
        "date": now.strftime("%A, %d.%m.%Y"),
        "weather": weather,
        "today_events": today_events,
        "upcoming_events": upcoming_events,
        "recent_mail": recent_mail,
        "deliveries": delivery_mail,
        "yesterday_open_mail": open_mail,
    }
    if config.openai_api_key:
        try:
            return build_ai_briefing(config, context)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403, 429}:
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
        "'Today', 'Deliveries', 'Today's to-dos' und 'Approaching'. "
        "Alles außer Titel und Einleitung muss als kurze Bulletpoints erscheinen. "
        "Kein langer Brief, keine Begrüßung mit Leerzeilen, keine horizontalen Trennstriche, keine Tabellen. "
        "Packe Wetter als 1-2 Bulletpoints unter Today. Unter Deliveries: offene Bestellungen und Lieferungen "
        "aller Händler, zum Beispiel Amazon, Proraso oder Comics, mit Liefertermin und Trackinglink, falls vorhanden. "
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
        "temperature": 0.4,
    }
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
        "Guten Morgen Christian. Hier ist der kompakte Lageplan für heute.",
        "",
        "## Today",
        f"- {weather['label']}: aktuell {weather['current_temp_c']} °C, heute ca. {weather['low_c']} bis {weather['high_c']} °C.",
        f"- Regenwahrscheinlichkeit am Nachmittag: {weather['afternoon_rain_probability_pct']}%. {weather['umbrella_note']}",
    ]
    lines.extend(format_items(context["today_events"], "Heute steht nichts Kritisches im Kalender."))
    lines.extend(["", "## Deliveries"])
    lines.extend(format_delivery_items(context["deliveries"]))
    lines.extend(["", "## Today's to-dos"])
    lines.extend(format_open_mail_items(context["yesterday_open_mail"]))
    lines.extend(["", "## Approaching"])
    lines.extend(format_items(context["upcoming_events"], "Keine nahen Termine gefunden."))
    return "\n".join(lines)


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
    briefing = build_briefing(
        config,
        now,
        weather,
        today_events,
        upcoming_events,
        recent_mail,
        delivery_mail,
        open_mail,
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
