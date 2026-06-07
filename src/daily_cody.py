#!/usr/bin/env python3
"""Daily Cody: send a personal morning briefing from GitHub Actions."""

from __future__ import annotations

import base64
import datetime as dt
import email.message
import html
import json
import os
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
) -> str:
    context = {
        "date": now.strftime("%A, %d.%m.%Y"),
        "weather": weather,
        "today_events": today_events,
        "upcoming_events": upcoming_events,
        "recent_mail": recent_mail,
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
        "Schreibe ein knappes deutsches Daily Briefing nach dem Daily-Dover-Muster: "
        "Titel, ein warmer Einleitungssatz, Wetter, Heute, Today's to-dos, Approaching, "
        "und am Ende eine kurze Nachricht an Christian. Sei nützlich, konkret, freundlich, "
        "und erfinde keine Fakten. Hebe nur Aufgaben hervor, die aus Kalender, Mail oder Wetter ableitbar sind."
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
        "## Wetter",
        f"- {weather['label']}: aktuell {weather['current_temp_c']} °C, heute ca. {weather['low_c']} bis {weather['high_c']} °C.",
        f"- Regenwahrscheinlichkeit am Nachmittag: {weather['afternoon_rain_probability_pct']}%. {weather['umbrella_note']}",
        "",
        "## Today",
    ]
    lines.extend(format_items(context["today_events"], "Heute steht nichts Kritisches im Kalender."))
    lines.extend(["", "## Today's to-dos"])
    lines.extend(format_mail_items(context["recent_mail"]))
    lines.extend(["", "## Approaching"])
    lines.extend(format_items(context["upcoming_events"], "Keine nahen Termine gefunden."))
    lines.extend(["", "## Für Christian", "Einmal kurz scannen, dann kann der Tag losgehen."])
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
        "Wetter": "☔",
        "Today": "📅",
        "Today's to-dos": "✅",
        "Approaching": "🏃",
        "Für Christian": "💬",
        "For Christian": "💬",
    }
    for line in markdown_body.splitlines():
        escaped = html.escape(line)
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1><span class=\"title-icon\">📰</span>{html.escape(line[2:])}</h1>")
            html_lines.append("<hr class=\"rule\">")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            heading = html.escape(line[3:])
            icon = section_icons.get(line[3:].strip(), "")
            icon_html = f"<span class=\"section-icon\">{icon}</span>" if icon else ""
            html_lines.append(f"<h2>{icon_html}{heading}</h2>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{html.escape(line[2:])}</li>")
        elif line.strip():
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            class_name = " class=\"lead\"" if first_paragraph else ""
            html_lines.append(f"<p{class_name}>{escaped}</p>")
            first_paragraph = False
    if in_list:
        html_lines.append("</ul>")
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      color: #2b2f33;
      font-family: Arial, Helvetica, sans-serif;
      line-height: 1.42;
    }}
    .page {{
      max-width: 760px;
      margin: 0 auto;
      padding: 28px 32px 36px;
      border-top: 2px solid #b9c0c8;
      border-bottom: 2px solid #b9c0c8;
    }}
    h1 {{
      margin: 0 0 12px;
      padding: 0 0 10px;
      border-bottom: 1px solid #c7cdd4;
      font-size: 20px;
      line-height: 1.25;
      font-weight: 700;
      color: #202124;
    }}
    .title-icon {{
      display: inline-block;
      width: 26px;
      margin-right: 6px;
      font-size: 17px;
      vertical-align: 1px;
    }}
    .rule {{
      display: none;
    }}
    .lead {{
      margin: 18px 0 24px;
      color: #5f6368;
      font-size: 15px;
      font-style: italic;
    }}
    h2 {{
      margin: 24px 0 10px;
      font-size: 17px;
      line-height: 1.3;
      font-weight: 700;
      color: #303134;
    }}
    .section-icon {{
      display: inline-block;
      width: 26px;
      margin-right: 4px;
      font-size: 15px;
      font-weight: 400;
      vertical-align: 1px;
    }}
    p {{
      margin: 0 0 14px 30px;
      font-size: 15px;
    }}
    ul {{
      margin: 0 0 18px 32px;
      padding: 0;
    }}
    li {{
      margin: 5px 0;
      padding-left: 2px;
      font-size: 15px;
    }}
    strong, b {{
      font-weight: 700;
    }}
    @media (max-width: 640px) {{
      .page {{
        padding: 22px 20px 30px;
      }}
      h1 {{
        font-size: 19px;
      }}
      p, li {{
        font-size: 14px;
      }}
      ul, p {{
        margin-left: 24px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    {"\n".join(html_lines)}
  </div>
</body>
</html>"""


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
    briefing = build_briefing(config, now, weather, today_events, upcoming_events, recent_mail)

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
