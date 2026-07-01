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
import shlex
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import delivery_detection

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
OPEN_METEO_API = "https://api.open-meteo.com/v1/forecast"
DWD_WARNINGS_API = "https://www.dwd.de/DWD/warnungen/warnapp/json/warnings.json"
OPENAI_API = "https://api.openai.com/v1/chat/completions"
ARD_PROGRAM_API = "https://programm-api.ard.de/program/api/program"
ZDF_LIVE_TV_URL = "https://www.zdf.de/live-tv"
ROOT_DIR = Path(__file__).resolve().parent.parent
DELIVERY_STATUS_PATH = ROOT_DIR / "data" / "delivery_status.json"
REMINDERS_EXPORT_STATUS_PATH = ROOT_DIR / "data" / "reminders_export_status.json"
MORNING_QUOTES_PATH = ROOT_DIR / "data" / "morning_quotes.json"
APPLICATION_WIKI_SNAPSHOT_PATH = ROOT_DIR / "data" / "application_wiki_snapshot.json"
RESOLVED_TOPICS_PATH = ROOT_DIR / "data" / "resolved_topics.json"
WORLD_CUP_TEAM_ALIASES = {
    "australia": "australien",
    "australien": "australien",
    "austria": "oesterreich",
    "oesterreich": "oesterreich",
    "osterreich": "oesterreich",
    "österreich": "oesterreich",
    "belgien": "belgien",
    "belgium": "belgien",
    "bosnia and herzegovina": "bosnien herzegowina",
    "bosnien und herzegowina": "bosnien herzegowina",
    "brasilien": "brasilien",
    "brazil": "brasilien",
    "canada": "kanada",
    "cabo verde": "kap verde",
    "cape verde": "kap verde",
    "curacao": "curacao",
    "curaçao": "curacao",
    "czech republic": "tschechien",
    "czechia": "tschechien",
    "dr congo": "dr kongo",
    "dr kongo": "dr kongo",
    "democratic republic of congo": "dr kongo",
    "ecuador": "ecuador",
    "egypt": "aegypten",
    "aegypten": "aegypten",
    "ägypten": "aegypten",
    "elfenbeinkueste": "elfenbeinkueste",
    "elfenbeinküste": "elfenbeinkueste",
    "cote divoire": "elfenbeinkueste",
    "cote d ivoire": "elfenbeinkueste",
    "côte d ivoire": "elfenbeinkueste",
    "ivory coast": "elfenbeinkueste",
    "england": "england",
    "france": "frankreich",
    "frankreich": "frankreich",
    "germany": "deutschland",
    "deutschland": "deutschland",
    "ghana": "ghana",
    "haiti": "haiti",
    "ir iran": "iran",
    "iran": "iran",
    "iraq": "irak",
    "irak": "irak",
    "japan": "japan",
    "jordan": "jordanien",
    "jordanien": "jordanien",
    "kanada": "kanada",
    "katar": "katar",
    "korea republic": "suedkorea",
    "south korea": "suedkorea",
    "suedkorea": "suedkorea",
    "sudkorea": "suedkorea",
    "südkorea": "suedkorea",
    "kroatien": "kroatien",
    "croatia": "kroatien",
    "marokko": "marokko",
    "morocco": "marokko",
    "mexico": "mexiko",
    "mexiko": "mexiko",
    "netherlands": "niederlande",
    "niederlande": "niederlande",
    "new zealand": "neuseeland",
    "neuseeland": "neuseeland",
    "norway": "norwegen",
    "norwegen": "norwegen",
    "panama": "panama",
    "paraguay": "paraguay",
    "portugal": "portugal",
    "qatar": "katar",
    "saudi arabia": "saudi arabien",
    "saudi arabien": "saudi arabien",
    "schottland": "schottland",
    "scotland": "schottland",
    "senegal": "senegal",
    "south africa": "suedafrika",
    "suedafrika": "suedafrika",
    "sudafrika": "suedafrika",
    "südafrika": "suedafrika",
    "spain": "spanien",
    "spanien": "spanien",
    "sweden": "schweden",
    "schweden": "schweden",
    "switzerland": "schweiz",
    "schweiz": "schweiz",
    "tschechien": "tschechien",
    "tunisia": "tunesien",
    "tunesien": "tunesien",
    "türkei": "tuerkei",
    "turkei": "tuerkei",
    "tuerkei": "tuerkei",
    "turkiye": "tuerkei",
    "türkiye": "tuerkei",
    "uruguay": "uruguay",
    "usa": "usa",
    "united states": "usa",
    "vereinigte staaten": "usa",
    "uzbekistan": "usbekistan",
    "usbekistan": "usbekistan",
}


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
    openai_timeout_seconds: int
    openai_max_attempts: int
    allow_template_fallback: bool
    send_window_hour: int
    send_window_end_hour: int
    force_send: bool
    allow_duplicate: bool
    dry_run: bool
    include_undated_reminders: bool
    require_fresh_reminders: bool
    fail_on_stale_reminders: bool
    reminders_max_age_hours: int
    reminders_export_path: str
    refresh_stale_reminders: bool
    reminders_refresh_command: str
    reminders_refresh_timeout_seconds: int
    application_wiki_snapshot_path: str


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
        openai_model=getenv("OPENAI_MODEL", "gpt-5.5"),
        openai_timeout_seconds=int(getenv("OPENAI_TIMEOUT_SECONDS", "120")),
        openai_max_attempts=int(getenv("OPENAI_MAX_ATTEMPTS", "2")),
        allow_template_fallback=os.getenv("ALLOW_TEMPLATE_FALLBACK", "false").lower() == "true",
        send_window_hour=int(getenv("SEND_WINDOW_HOUR", "6")),
        send_window_end_hour=int(getenv("SEND_WINDOW_END_HOUR", "9")),
        force_send=os.getenv("FORCE_SEND", "false").lower() == "true",
        allow_duplicate=os.getenv("ALLOW_DUPLICATE", "false").lower() == "true",
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true",
        include_undated_reminders=os.getenv("INCLUDE_UNDATED_REMINDERS", "false").lower() == "true",
        require_fresh_reminders=os.getenv("REQUIRE_FRESH_REMINDERS", "false").lower() == "true",
        fail_on_stale_reminders=os.getenv("FAIL_ON_STALE_REMINDERS", "false").lower() == "true",
        reminders_max_age_hours=int(getenv("REMINDERS_MAX_AGE_HOURS", "60")),
        reminders_export_path=getenv("REMINDERS_EXPORT_PATH", "data/reminders.json"),
        refresh_stale_reminders=os.getenv("REFRESH_STALE_REMINDERS", "true").lower() == "true",
        reminders_refresh_command=getenv(
            "REMINDERS_REFRESH_COMMAND", "scripts/export_apple_reminders_if_window.sh"
        ),
        reminders_refresh_timeout_seconds=int(getenv("REMINDERS_REFRESH_TIMEOUT_SECONDS", "180")),
        application_wiki_snapshot_path=getenv(
            "APPLICATION_WIKI_SNAPSHOT_PATH", "data/application_wiki_snapshot.json"
        ),
    )


def request_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    data = None
    request_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read().decode("utf-8")
    return json.loads(payload) if payload else {}


def request_text(url: str, *, headers: dict[str, str] | None = None) -> str:
    request_headers = {
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "User-Agent": "Daily-Cody/1.0",
        **(headers or {}),
    }
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


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
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.reason
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
            error = error_payload.get("error")
            description = error_payload.get("error_description")
            if error and description:
                detail = f"{error}: {description}"
            elif error:
                detail = str(error)
            if error == "invalid_grant":
                detail = (
                    f"{detail}. Refresh token is no longer valid. Check whether the Google "
                    "OAuth consent screen is still in Testing status, the user revoked access, "
                    "or GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET were changed."
                )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        raise RuntimeError(f"Google token refresh failed with HTTP {exc.code}: {detail}") from exc
    return payload["access_token"]


def get_weather(config: Config) -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "latitude": config.weather_latitude,
            "longitude": config.weather_longitude,
            "timezone": config.timezone,
            "current": "temperature_2m,apparent_temperature,precipitation,rain,weather_code,wind_gusts_10m",
            "hourly": "temperature_2m,precipitation_probability,precipitation,rain,wind_gusts_10m",
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,precipitation_sum,wind_gusts_10m_max"
            ),
            "forecast_days": "4",
        }
    )
    data = request_json(f"{OPEN_METEO_API}?{params}")
    hourly = data.get("hourly", {})
    daily = data.get("daily", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    rain_probs = hourly.get("precipitation_probability", [])
    afternoon_probs = [
        rain_probs[index]
        for index, timestamp in enumerate(times)
        if "12:00" <= timestamp[-5:] <= "18:00" and index < len(rain_probs)
    ]
    max_afternoon_rain = max(afternoon_probs) if afternoon_probs else None
    current = data.get("current", {})
    current_temp = current.get("temperature_2m")
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    today_wind_gust = first_list_value(daily.get("wind_gusts_10m_max", []))
    high = highs[0] if highs else max(temps[:24]) if temps else None
    low = lows[0] if lows else min(temps[:24]) if temps else None
    place = weather_place(config.weather_label)
    warnings = list_weather_warnings(config, dt.datetime.now(ZoneInfo(config.timezone)))
    compact_warnings = compact_weather_warnings(warnings)
    next_days = build_weather_daily_items(daily, config.timezone)
    summary = build_weather_summary(
        place,
        current_temp,
        current.get("apparent_temperature"),
        low,
        high,
        max_afternoon_rain,
        today_wind_gust,
        compact_warnings,
    )
    return {
        "label": config.weather_label,
        "place": place,
        "current_temp_c": current_temp,
        "apparent_temp_c": current.get("apparent_temperature"),
        "high_c": high,
        "low_c": low,
        "afternoon_rain_probability_pct": max_afternoon_rain,
        "current_precipitation_mm": current.get("precipitation"),
        "current_rain_mm": current.get("rain"),
        "current_weather_code": current.get("weather_code"),
        "current_condition": describe_weather_code(current.get("weather_code")),
        "current_wind_gust_kmh": current.get("wind_gusts_10m"),
        "today_wind_gust_kmh": today_wind_gust,
        "today": {
            "condition": next_days[0]["condition"] if next_days else "",
            "current_temp_c": current_temp,
            "apparent_temp_c": current.get("apparent_temperature"),
            "low_c": low,
            "high_c": high,
            "afternoon_rain_probability_pct": max_afternoon_rain,
            "precipitation_sum_mm": first_list_value(daily.get("precipitation_sum", [])),
            "wind_gust_kmh": today_wind_gust,
        },
        "next_days": next_days,
        "warnings": compact_warnings,
        "summary": summary,
    }


def weather_place(label: str) -> str:
    return "Hamburg" if "hamburg" in label.lower() else label


def build_weather_daily_items(daily: dict[str, Any], timezone: str) -> list[dict[str, Any]]:
    dates = daily.get("time", [])
    codes = daily.get("weather_code", [])
    highs = daily.get("temperature_2m_max", [])
    lows = daily.get("temperature_2m_min", [])
    rain_probs = daily.get("precipitation_probability_max", [])
    precipitation = daily.get("precipitation_sum", [])
    gusts = daily.get("wind_gusts_10m_max", [])
    items = []
    for index, raw_date in enumerate(dates[:4]):
        code = list_value(codes, index)
        items.append(
            {
                "date": str(raw_date),
                "day": format_relative_weather_day(raw_date, timezone),
                "condition": describe_weather_code(code),
                "weather_code": code,
                "low_c": list_value(lows, index),
                "high_c": list_value(highs, index),
                "rain_probability_pct": list_value(rain_probs, index),
                "precipitation_sum_mm": list_value(precipitation, index),
                "wind_gust_kmh": list_value(gusts, index),
            }
        )
    return items


def compact_weather_warnings(warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for warning in warnings[:3]:
        event = format_warning_event(warning.get("event") or warning.get("headline") or "Wetterwarnung")
        description = clean_warning_text(warning.get("description", ""))
        compact.append(
            {
                "event": event,
                "headline": clean_warning_text(warning.get("headline", "")),
                "start_time": format_warning_time(warning.get("start")),
                "end_time": format_warning_time(warning.get("end")),
                "description": shorten_warning_description(description),
                "level": warning.get("level"),
                "state": warning.get("stateShort") or warning.get("state"),
            }
        )
    return compact


def describe_weather_code(code: Any) -> str:
    if not isinstance(code, int):
        return ""
    if code == 0:
        return "klar"
    if code == 1:
        return "überwiegend klar"
    if code == 2:
        return "teils bewölkt"
    if code == 3:
        return "bewölkt"
    if code in {45, 48}:
        return "neblig"
    if 51 <= code <= 57:
        return "Nieselregen"
    if 61 <= code <= 67:
        return "Regen"
    if 71 <= code <= 77:
        return "Schnee"
    if 80 <= code <= 82:
        return "Regenschauer"
    if 85 <= code <= 86:
        return "Schneeschauer"
    if 95 <= code <= 99:
        return "Gewitter"
    return "wechselhaft"


def build_weather_summary(
    place: str,
    current_temp: Any,
    apparent_temp: Any,
    low: Any,
    high: Any,
    rain_probability: Any,
    wind_gust_kmh: Any,
    warnings: list[dict[str, Any]] | None = None,
) -> str:
    parts = [f"{place}: aktuell {format_temp(current_temp)}"]
    if isinstance(apparent_temp, (int, float)) and isinstance(current_temp, (int, float)):
        if abs(apparent_temp - current_temp) >= 2:
            parts.append(f"gefühlt {format_temp(apparent_temp)}")
    if low is not None or high is not None:
        parts.append(f"Tagesbereich {format_temp(low)} bis {format_temp(high)}")
    if rain_probability is not None:
        parts.append(f"Regenrisiko am Nachmittag {format_percent(rain_probability)}")
    if wind_gust_kmh is not None:
        parts.append(f"Böen bis {format_speed(wind_gust_kmh)}")
    for warning in (warnings or [])[:2]:
        event = warning.get("event", "Wetterwarnung")
        start = warning.get("start_time")
        end = warning.get("end_time")
        if start and end:
            parts.append(f"Warnung {event} {start}-{end}")
        else:
            parts.append(f"Warnung {event}")
    return "; ".join(parts) + "."


def format_relative_weather_day(raw_date: Any, timezone: str) -> str:
    try:
        value = dt.date.fromisoformat(str(raw_date))
    except ValueError:
        return "kommenden Tag"
    today = dt.datetime.now(ZoneInfo(timezone)).date()
    if value == today:
        return "heute"
    if value == today + dt.timedelta(days=1):
        return "morgen"
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    return weekdays[value.weekday()]


def list_weather_warnings(config: Config, now: dt.datetime) -> list[dict[str, Any]]:
    try:
        payload = request_text(DWD_WARNINGS_API)
        data = parse_dwd_warning_payload(payload)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        print(f"Weather warning lookup skipped: {exc}", file=sys.stderr)
        return []

    warnings = []
    now_ms = int(now.timestamp() * 1000)
    cutoff_ms = int((now + dt.timedelta(hours=36)).timestamp() * 1000)
    for warning_list in data.get("warnings", {}).values():
        if not isinstance(warning_list, list):
            continue
        for warning in warning_list:
            if not isinstance(warning, dict) or not is_hamburg_warning(warning):
                continue
            start = warning.get("start")
            end = warning.get("end")
            if isinstance(end, (int, float)) and end < now_ms:
                continue
            if isinstance(start, (int, float)) and start > cutoff_ms:
                continue
            warnings.append(warning)

    warnings.sort(key=lambda item: (item.get("start") or 0, -(item.get("level") or 0)))
    unique_warnings = []
    seen = set()
    for warning in warnings:
        key = weather_warning_key(warning)
        if key in seen:
            continue
        seen.add(key)
        unique_warnings.append(warning)
        if len(unique_warnings) == 3:
            break
    return unique_warnings


def parse_dwd_warning_payload(payload: str) -> dict[str, Any]:
    match = re.search(r"warnWetter\.loadWarnings\((.*)\)\s*;?\s*$", payload, re.S)
    if not match:
        raise ValueError("DWD warning payload has unexpected format")
    data = json.loads(match.group(1))
    if not isinstance(data, dict):
        raise ValueError("DWD warning payload is not an object")
    return data


def is_hamburg_warning(warning: dict[str, Any]) -> bool:
    state = str(warning.get("state", "")).lower()
    state_short = str(warning.get("stateShort", "")).lower()
    return state_short == "hh" or state == "hamburg"


def format_weather_warnings(warnings: list[dict[str, Any]]) -> str:
    if not warnings:
        return ""
    formatted = []
    seen = set()
    for warning in warnings:
        key = weather_warning_key(warning)
        if key in seen:
            continue
        seen.add(key)
        formatted.append(format_weather_warning(warning))
        if len(formatted) == 2:
            break
    formatted = [item for item in formatted if item]
    if not formatted:
        return ""
    return "Warnung: " + " ".join(formatted)


def weather_warning_key(warning: dict[str, Any]) -> tuple[str, str, Any, Any]:
    return (
        clean_warning_text(warning.get("event", "")).lower(),
        clean_warning_text(warning.get("description", "")).lower(),
        warning.get("start"),
        warning.get("end"),
    )


def format_weather_warning(warning: dict[str, Any]) -> str:
    event = format_warning_event(warning.get("event") or warning.get("headline") or "Wetterwarnung")
    description = clean_warning_text(warning.get("description", ""))
    start = format_warning_time(warning.get("start"))
    end = format_warning_time(warning.get("end"))
    time_text = ""
    if start and end:
        time_text = f" von {start} bis {end}"
    elif start:
        time_text = f" ab {start}"
    elif end:
        time_text = f" bis {end}"
    detail = shorten_warning_description(description)
    if detail:
        return f"{event}{time_text}: {detail}."
    return f"{event}{time_text}."


def format_warning_event(value: Any) -> str:
    text = clean_warning_text(value)
    if text.isupper():
        return text.title()
    return text


def clean_warning_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .")


def shorten_warning_description(description: str) -> str:
    if not description:
        return ""
    first_sentence = re.split(r"(?<!\d\.)(?<=[.!?])\s+", description.strip(), maxsplit=1)[0]
    return first_sentence[:180].rstrip(" .,;")


def format_warning_time(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return dt.datetime.fromtimestamp(value / 1000, tz=ZoneInfo("Europe/Berlin")).strftime("%H:%M")


def first_list_value(values: Any) -> Any:
    if isinstance(values, list) and values:
        return values[0]
    return None


def list_value(values: Any, index: int) -> Any:
    if isinstance(values, list) and index < len(values):
        return values[index]
    return None


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


def list_world_cup_games(
    config: Config, now: dt.datetime, today_events: list[dict[str, Any]]
) -> list[dict[str, str]]:
    calendar_games = extract_world_cup_calendar_games(today_events, config.timezone)
    broadcasts = list_world_cup_free_tv_broadcasts(config, now)
    if not calendar_games:
        return broadcasts_for_date(broadcasts, now.date())

    output = []
    for game in calendar_games:
        broadcast = find_matching_world_cup_broadcast(game, broadcasts)
        merged = dict(game)
        if broadcast:
            merged["free_tv"] = broadcast["free_tv"]
            merged["broadcast_title"] = broadcast.get("title", "")
            if not merged.get("kickoff") and broadcast.get("kickoff"):
                merged["kickoff"] = broadcast["kickoff"]
        else:
            merged["free_tv"] = game.get("free_tv") or "nicht bei ARD/ZDF gefunden"
        output.append(merged)
    return sorted(output, key=lambda item: item.get("sort_key", item.get("kickoff", "")))


def extract_world_cup_calendar_games(
    events: list[dict[str, Any]], timezone: str
) -> list[dict[str, str]]:
    games = []
    seen = set()
    for event in events:
        if not is_world_cup_calendar_event(event):
            continue
        summary = str(event.get("summary", "")).strip()
        fixture = extract_fixture_title(summary)
        if not fixture:
            fixture = extract_fixture_title(str(event.get("description", "")))
        teams = parse_fixture_teams(fixture)
        if not fixture or not teams:
            continue
        start_value = str(event.get("start", ""))
        start_dt = parse_datetime_text(start_value, timezone)
        sort_key = start_dt.isoformat() if start_dt else start_value
        key = tuple(sorted(teams)) + (sort_key[:10],)
        if key in seen:
            continue
        seen.add(key)
        event_text = " ".join(
            str(event.get(field, ""))
            for field in ("summary", "description", "location", "calendar")
        )
        games.append(
            {
                "fixture": fixture,
                "teams": list(teams),
                "kickoff": format_time(start_value) if start_value else "",
                "start": start_value,
                "sort_key": sort_key,
                "free_tv": extract_free_tv_sender_from_text(event_text),
                "source": "calendar",
            }
        )
    return games


def is_world_cup_calendar_event(event: dict[str, Any]) -> bool:
    text = " ".join(
        str(event.get(field, ""))
        for field in ("calendar", "summary", "description", "location")
    )
    normalized = normalize_search_text(text)
    has_world_cup_marker = any(
        marker in normalized
        for marker in (
            "fifa wm",
            "fifa world cup",
            "fussball wm",
            "fussball weltmeisterschaft",
            "world cup",
            "weltmeisterschaft",
            "wm 2026",
            "mixedcup2026",
        )
    )
    return has_world_cup_marker and looks_like_match_text(text)


def filter_world_cup_events_from_calendar(
    events: list[dict[str, Any]], world_cup_games: list[dict[str, str]]
) -> list[dict[str, Any]]:
    if not world_cup_games:
        return events
    return [event for event in events if not is_world_cup_calendar_event(event)]


def list_world_cup_free_tv_broadcasts(config: Config, now: dt.datetime) -> list[dict[str, str]]:
    broadcasts: list[dict[str, str]] = []
    for loader in (list_ard_world_cup_broadcasts, list_zdf_world_cup_broadcasts):
        try:
            broadcasts.extend(loader(config, now))
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as exc:
            print(f"World Cup TV schedule unavailable: {exc}", file=sys.stderr)
    return dedupe_world_cup_broadcasts(broadcasts)


def list_ard_world_cup_broadcasts(config: Config, now: dt.datetime) -> list[dict[str, str]]:
    broadcasts = []
    for day in (now.date() - dt.timedelta(days=1), now.date()):
        params = urllib.parse.urlencode({"day": day.isoformat()})
        data = request_json(f"{ARD_PROGRAM_API}?{params}")
        for item in iter_ard_epg_items(data):
            channel = str(item.get("channel", {}).get("name", ""))
            if channel != "Das Erste":
                continue
            title = str(item.get("coreTitle") or item.get("title") or "")
            text = " ".join(
                str(item.get(field, ""))
                for field in ("title", "coreTitle", "subline", "synopsis")
            )
            if "fifa wm 2026" not in normalize_search_text(text):
                continue
            if not looks_like_match_text(text):
                continue
            fixture = extract_fixture_title(title)
            teams = parse_fixture_teams(fixture)
            if not fixture or not teams:
                continue
            start_value = str(item.get("broadcastedOn") or item.get("beginNet") or "")
            start_dt = parse_datetime_text(start_value, config.timezone)
            kickoff = extract_kickoff_from_text(text)
            broadcasts.append(
                {
                    "fixture": fixture,
                    "teams": list(teams),
                    "kickoff": kickoff,
                    "kickoff_date": start_dt.date().isoformat() if start_dt else "",
                    "broadcast_start": start_value,
                    "free_tv": "ARD",
                    "title": title,
                    "source": "ard_programm",
                }
            )
    return broadcasts


def iter_ard_epg_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    for channel in data.get("channels", []):
        if not isinstance(channel, dict):
            continue
        for slot in channel.get("timeSlots", []):
            if not isinstance(slot, list):
                continue
            items.extend(item for item in slot if isinstance(item, dict))
    return items


def list_zdf_world_cup_broadcasts(config: Config, now: dt.datetime) -> list[dict[str, str]]:
    page = request_text(ZDF_LIVE_TV_URL)
    return parse_zdf_world_cup_broadcasts(page, config.timezone)


def parse_zdf_world_cup_broadcasts(page: str, timezone: str) -> list[dict[str, str]]:
    text = html.unescape(page)
    text = text.replace('\\"', '"').replace("\\/", "/").replace("\\u0026", "&")
    pattern = re.compile(
        r'"title":"Fußball-WM 2026:\s*(?P<title>[^"]+)"'
        r'.{0,26000}?"currentMediaType":"[^"]*"'
        r'.{0,5000}?"editorialDate":"(?P<editorial>[^"]+)",'
        r'"teaser":\{"__typename":"VideoTeaser","title":"(?P<teaser>[^"]+)",'
        r'"description":"(?P<description>[^"]*)"',
        flags=re.S,
    )
    broadcasts = []
    for match in pattern.finditer(text):
        title = match.group("title")
        if re.search(r"\b(taktik|pressekonferenz)\b", title, flags=re.I):
            continue
        fixture = extract_fixture_title(title)
        teams = parse_fixture_teams(fixture)
        if not fixture or not teams:
            continue
        editorial = match.group("editorial")
        editorial_dt = parse_datetime_text(editorial, timezone)
        description = match.group("description")
        kickoff = extract_kickoff_from_text(description)
        kickoff_date = infer_kickoff_date(editorial_dt, kickoff)
        broadcasts.append(
            {
                "fixture": fixture,
                "teams": list(teams),
                "kickoff": kickoff or (editorial_dt.strftime("%H:%M") if editorial_dt else ""),
                "kickoff_date": kickoff_date.isoformat() if kickoff_date else "",
                "broadcast_start": editorial,
                "free_tv": "ZDF",
                "title": f"Fußball-WM 2026: {fixture}",
                "source": "zdf_live_tv",
            }
        )
    return broadcasts


def infer_kickoff_date(editorial_dt: dt.datetime | None, kickoff: str) -> dt.date | None:
    if not editorial_dt:
        return None
    if not kickoff:
        return editorial_dt.date()
    try:
        hour = int(kickoff.split(":", 1)[0])
    except ValueError:
        return editorial_dt.date()
    if hour < 6 and editorial_dt.hour >= 18:
        return editorial_dt.date() + dt.timedelta(days=1)
    return editorial_dt.date()


def dedupe_world_cup_broadcasts(items: list[dict[str, str]]) -> list[dict[str, str]]:
    output = []
    seen = set()
    for item in items:
        teams = tuple(sorted(item.get("teams", [])))
        key = (teams, item.get("free_tv", ""), item.get("kickoff_date", ""))
        if not teams or key in seen:
            continue
        seen.add(key)
        output.append(item)
    return output


def broadcasts_for_date(items: list[dict[str, str]], today: dt.date) -> list[dict[str, str]]:
    output = []
    for item in items:
        date_text = item.get("kickoff_date", "")
        if not date_text:
            date_text = item.get("broadcast_start", "")[:10]
        try:
            item_date = dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        if item_date == today:
            output.append({**item, "sort_key": item.get("broadcast_start", item.get("kickoff", ""))})
    return sorted(output, key=lambda item: item.get("sort_key", item.get("kickoff", "")))


def find_matching_world_cup_broadcast(
    game: dict[str, str], broadcasts: list[dict[str, str]]
) -> dict[str, str] | None:
    game_teams = set(game.get("teams", []))
    if not game_teams:
        return None
    for item in broadcasts:
        if set(item.get("teams", [])) == game_teams:
            return item
    return None


def extract_fixture_title(value: str) -> str:
    title = html.unescape(" ".join(str(value or "").split()))
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"^Match\s+\d+\s*:?\s*", "", title, flags=re.I)
    title = re.sub(r"^(?:FIFA\s*)?(?:Fußball-?)?WM\s*2026\s*:?\s*", "", title, flags=re.I)
    title = re.sub(r"^(?:FIFA\s*)?World\s+Cup\s*2026\s*:?\s*", "", title, flags=re.I)
    title = re.sub(r"^(?:Vorrunde\s*)?(?:Gruppe|Gr\.?)\s+[A-L]\s*:?\s*", "", title, flags=re.I)
    title = re.sub(r"^Eröffnung(?:sfeier)?\s+(?:und\s+)?(?:das\s+)?Spiel\s+", "", title, flags=re.I)
    title = re.sub(r"\s+\([^)]*\)$", "", title)
    match = re.search(
        r"(.+?)(?:\s+[-–—]\s+|\s*[–—]\s*|\s+\bvs\.?\b\s+|\s+\bv\.?\b\s+|\s+\bgegen\b\s+)(.+)",
        title,
        flags=re.I,
    )
    if not match:
        return ""
    team_one = clean_team_segment(match.group(1))
    team_two = clean_team_segment(match.group(2))
    if not team_one or not team_two:
        return ""
    return f"{team_one} - {team_two}"


def clean_team_segment(value: str) -> str:
    value = re.sub(r".*:\s*", "", value)
    value = re.sub(r"\s+in\s+der\s+.*$", "", value, flags=re.I)
    value = re.sub(r"\s+im\s+.*$", "", value, flags=re.I)
    value = re.sub(r"\s+\|.*$", "", value)
    value = re.sub(r"\s+", " ", value).strip(" .:;,-–—")
    return value


def parse_fixture_teams(fixture: str) -> tuple[str, ...]:
    match = re.search(r"(.+?)(?:\s+[-–—]\s+|\s*[–—]\s*)(.+)", fixture)
    if not match:
        return ()
    team_one = canonical_team(match.group(1))
    team_two = canonical_team(match.group(2))
    if not team_one or not team_two:
        return ()
    return (team_one, team_two)


def canonical_team(value: str) -> str:
    normalized = normalize_search_text(value)
    normalized = re.sub(
        r"\b(nationalmannschaft|national team|gruppe|group|vorrunde|eröffnungsspiel|eroeffnungsspiel)\b",
        " ",
        normalized,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in WORLD_CUP_TEAM_ALIASES:
        return WORLD_CUP_TEAM_ALIASES[normalized]
    for alias, canonical in sorted(WORLD_CUP_TEAM_ALIASES.items(), key=lambda item: -len(item[0])):
        if alias in normalized:
            return canonical
    return normalized


def extract_free_tv_sender_from_text(text: str) -> str:
    normalized = normalize_search_text(text)
    senders = []
    if re.search(r"\b(zdf|zweites|zweiten)\b", normalized):
        senders.append("ZDF")
    if re.search(r"\b(ard|das erste|ersten)\b", normalized):
        senders.append("ARD")
    return "/".join(senders)


def extract_kickoff_from_text(text: str) -> str:
    match = re.search(
        r"(?:Spielbeginn|Anstoß|Anstoss)(?:\s+ist)?(?:\s+um|:)?\s+(\d{1,2})(?::|\.| Uhr)?(\d{2})?",
        text,
        flags=re.I,
    )
    if not match:
        return ""
    hour = int(match.group(1))
    minute = int(match.group(2) or "00")
    return f"{hour:02d}:{minute:02d}"


def looks_like_match_text(text: str) -> bool:
    return bool(re.search(r"\s(?:-|–|—|vs\.?|v\.?|gegen)\s|\S\s*[–—]\s*\S", text, flags=re.I))


def parse_datetime_text(value: str, timezone: str) -> dt.datetime | None:
    if not value:
        return None
    zone = ZoneInfo(timezone)
    try:
        if len(value) == 10:
            return dt.datetime.fromisoformat(value).replace(tzinfo=zone)
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(zone)
    except ValueError:
        return None


def normalize_search_text(value: str) -> str:
    value = value.replace("ß", "ss").replace("ẞ", "ss")
    normalized = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    without_marks = without_marks.lower()
    without_marks = re.sub(r"[^a-z0-9]+", " ", without_marks)
    return re.sub(r"\s+", " ", without_marks).strip()


def read_exported_reminders(config: Config, now: dt.datetime) -> tuple[list[dict[str, str]], str | None]:
    path = resolve_reminders_export_path(config)
    if should_refresh_reminders_export(config, now, path):
        refresh_error = refresh_reminders_export(config)
        if refresh_error:
            print(refresh_error, file=sys.stderr)
    if not path.exists():
        message = f"Apple Reminders export missing at {path}; Apple Reminders skipped for this briefing."
        print(message, file=sys.stderr)
        return [], message if config.require_fresh_reminders else None
    freshness_warning = get_reminders_export_freshness_warning(config, now)
    if freshness_warning:
        if config.require_fresh_reminders and config.fail_on_stale_reminders:
            raise RuntimeError(f"{freshness_warning} No briefing sent with stale Reminders data.")
        if config.require_fresh_reminders:
            message = f"{freshness_warning} Apple Reminders skipped for this briefing."
            print(message, file=sys.stderr)
            return [], message
        print(freshness_warning, file=sys.stderr)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"Apple Reminders export unavailable: {exc}"
        print(message, file=sys.stderr)
        return [], message if config.require_fresh_reminders else None

    reminders = []
    for raw in iter_reminder_records(data):
        reminder = normalize_exported_reminder(raw, now, config.include_undated_reminders)
        if reminder:
            reminders.append(reminder)

    reminders.sort(key=lambda item: (item["sort_key"], item["title"].lower()))
    return [{key: value for key, value in item.items() if key != "sort_key"} for item in reminders[:12]], None


def resolve_reminders_export_path(config: Config) -> Path:
    path = Path(config.reminders_export_path)
    return path if path.is_absolute() else ROOT_DIR / path


def should_refresh_reminders_export(config: Config, now: dt.datetime, path: Path) -> bool:
    if not config.refresh_stale_reminders:
        return False
    if not path.exists():
        return True
    return get_reminders_export_freshness_warning(config, now) is not None


def refresh_reminders_export(config: Config) -> str | None:
    command = shlex.split(config.reminders_refresh_command)
    if not command:
        return "Apple Reminders refresh skipped: REMINDERS_REFRESH_COMMAND is empty."
    command[0] = resolve_repo_command(command[0])
    try:
        result = subprocess.run(
            command,
            cwd=ROOT_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, config.reminders_refresh_timeout_seconds),
            check=False,
        )
    except FileNotFoundError as exc:
        return f"Apple Reminders refresh unavailable: {exc}"
    except subprocess.TimeoutExpired:
        return (
            "Apple Reminders refresh timed out after "
            f"{max(1, config.reminders_refresh_timeout_seconds)} seconds."
        )
    if result.returncode == 0:
        return None
    details = strip_long((result.stderr or result.stdout or "").strip(), 500)
    if details:
        return f"Apple Reminders refresh failed with exit code {result.returncode}: {details}"
    return f"Apple Reminders refresh failed with exit code {result.returncode}."


def resolve_repo_command(command: str) -> str:
    if os.path.isabs(command):
        return command
    if "/" not in command:
        return command
    return str(ROOT_DIR / command)


def get_reminders_export_freshness_warning(config: Config, now: dt.datetime) -> str | None:
    freshness = read_reminders_export_freshness(now)
    max_age = dt.timedelta(hours=max(1, config.reminders_max_age_hours))
    if freshness is not None and freshness <= max_age:
        return None
    if freshness is None:
        message = "Apple Reminders export status missing; data/reminders.json may be stale."
    else:
        message = (
            "Apple Reminders export is stale: "
            f"{freshness.total_seconds() / 3600:.1f} hours old; max is {max_age.total_seconds() / 3600:.0f}."
        )
    return message


def read_reminders_export_freshness(now: dt.datetime) -> dt.timedelta | None:
    if not REMINDERS_EXPORT_STATUS_PATH.exists():
        return None
    try:
        data = json.loads(REMINDERS_EXPORT_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Apple Reminders export status unavailable: {exc}", file=sys.stderr)
        return None
    exported_at = str(data.get("exported_at") or "").strip()
    if not exported_at:
        return None
    try:
        exported = dt.datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if exported.tzinfo is None:
        exported = exported.replace(tzinfo=dt.UTC)
    return now.astimezone(dt.UTC) - exported.astimezone(dt.UTC)


def read_application_wiki_snapshot(config: Config) -> dict[str, Any]:
    path = Path(config.application_wiki_snapshot_path)
    if not path.is_absolute():
        path = ROOT_DIR / path
    if not path.exists():
        return {"items": [], "waiting_for": [], "action_overrides": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Application wiki snapshot unavailable: {exc}", file=sys.stderr)
        return {"items": [], "waiting_for": [], "action_overrides": []}
    if not isinstance(data, dict):
        return {"items": [], "waiting_for": [], "action_overrides": []}
    for key in ("items", "waiting_for", "action_overrides"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def split_reminders_for_briefing(
    reminders: list[dict[str, str]], now: dt.datetime
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    today = now.date()
    today_todos = []
    waiting = []
    later = []
    for reminder in reminders:
        due = parse_short_due_date(reminder.get("due", ""), now)
        if due and due <= today:
            today_todos.append(reminder)
        elif looks_like_waiting_reminder(reminder):
            waiting.append(reminder)
        else:
            later.append(reminder)
    return today_todos, later, waiting


def iter_reminder_records(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("reminders", "items", "data", "tasks"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    records = []
    for value in data.values():
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            records.extend(iter_reminder_records(value))
    return records


def normalize_exported_reminder(
    raw: dict[str, Any], now: dt.datetime, include_undated: bool = False
) -> dict[str, str] | None:
    if is_completed_reminder(raw):
        return None
    title = first_text(raw, ("title", "name", "summary", "text"))
    if not title:
        return None
    due = extract_reminder_due(raw, now)
    due = roll_recurring_due_forward(raw, due, now)
    notes = first_text(raw, ("notes", "note", "body", "description"))
    list_name = first_text(raw, ("list", "listName", "list_name", "calendar", "calendarName"))
    today = now.date()
    is_friday_planning = now.weekday() == 4
    if due:
        days_until = (due.date() - today).days
        max_days = 7 if is_friday_planning else 2
        if days_until > max_days:
            return None
        due_label = format_short_reminder_date(due.date())
        sort_key = f"0-{due.isoformat()}"
    else:
        if not include_undated:
            return None
        due_label = ""
        sort_key = f"1-{title.lower()}"
    return {
        "title": strip_long(title, 120),
        "due": due_label,
        "list": strip_long(list_name, 60),
        "notes": strip_long(notes, 140),
        "sort_key": sort_key,
    }


def extract_reminder_due(raw: dict[str, Any], now: dt.datetime) -> dt.datetime | None:
    for key in (
        "due",
        "dueDate",
        "due_date",
        "date",
        "deadline",
        "remind_me_date",
        "reminderDate",
        "reminder_date",
        "alarm_date",
    ):
        parsed = parse_reminder_due(raw.get(key), now)
        if parsed:
            return parsed
    alarms = raw.get("alarms")
    if isinstance(alarms, list):
        alarm_dates = []
        for alarm in alarms:
            if not isinstance(alarm, dict):
                continue
            parsed = parse_reminder_due(
                first_present(alarm, ("absolute_date", "date", "dateTime", "datetime", "timestamp")),
                now,
            )
            if parsed:
                alarm_dates.append(parsed)
        if alarm_dates:
            return min(alarm_dates)
    return None


def roll_recurring_due_forward(
    raw: dict[str, Any], due: dt.datetime | None, now: dt.datetime
) -> dt.datetime | None:
    if not due or due.date() >= now.date() or not is_recurring_reminder(raw):
        return due
    rules = raw.get("recurrence_rules")
    if not isinstance(rules, list):
        return due
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        frequency = str(rule.get("frequency", "")).lower()
        interval = parse_positive_int(rule.get("interval"), default=1)
        if frequency == "daily":
            return combine_due_date_time(now.date(), due)
        if frequency == "weekly":
            return next_weekly_reminder_due(raw, rule, due, now, interval)
    return due


def is_recurring_reminder(raw: dict[str, Any]) -> bool:
    value = raw.get("recurring")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "yes", "1"}:
        return True
    return isinstance(raw.get("recurrence_rules"), list) and bool(raw.get("recurrence_rules"))


def parse_positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def next_weekly_reminder_due(
    raw: dict[str, Any],
    rule: dict[str, Any],
    due: dt.datetime,
    now: dt.datetime,
    interval: int,
) -> dt.datetime:
    weekdays = reminder_rule_weekdays(rule, due)
    start_date = due.date()
    today = now.date()
    for offset in range(0, 370):
        candidate = today + dt.timedelta(days=offset)
        if candidate.weekday() not in weekdays:
            continue
        weeks_since_start = max(0, (candidate - start_date).days // 7)
        if weeks_since_start % interval == 0:
            return combine_due_date_time(candidate, due)
    return combine_due_date_time(today, due)


def reminder_rule_weekdays(rule: dict[str, Any], due: dt.datetime) -> set[int]:
    raw_days = rule.get("days_of_week")
    if not isinstance(raw_days, list) or not raw_days:
        return {due.weekday()}
    mapping = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    weekdays = {mapping[str(day).lower()] for day in raw_days if str(day).lower() in mapping}
    return weekdays or {due.weekday()}


def combine_due_date_time(value: dt.date, original: dt.datetime) -> dt.datetime:
    return dt.datetime.combine(value, original.timetz()).replace(tzinfo=original.tzinfo)


def is_completed_reminder(raw: dict[str, Any]) -> bool:
    for key in ("completed", "isCompleted", "done", "is_done"):
        value = raw.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "yes", "1"}:
            return True
    return False


def first_present(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    value = first_present(raw, keys)
    if isinstance(value, dict):
        return first_text(value, ("title", "name", "summary", "value"))
    return str(value).strip() if value is not None else ""


def parse_reminder_due(value: Any, now: dt.datetime) -> dt.datetime | None:
    if not value:
        return None
    zone = now.tzinfo
    if isinstance(value, dict):
        for key in ("dateTime", "datetime", "date", "value", "timestamp"):
            parsed = parse_reminder_due(value.get(key), now)
            if parsed:
                return parsed
        return None
    if isinstance(value, (int, float)):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return dt.datetime.fromtimestamp(timestamp, tz=zone)
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return dt.datetime.fromisoformat(text).replace(tzinfo=zone)
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(zone)
    except ValueError:
        return None


def parse_short_due_date(value: str, now: dt.datetime) -> dt.date | None:
    match = re.fullmatch(r"(?:[A-Za-zÄÖÜäöü]{2}\s+)?(\d{1,2})\.(\d{1,2})\.?", value.strip())
    if not match:
        return None
    day = int(match.group(1))
    month = int(match.group(2))
    try:
        due = dt.date(now.year, month, day)
    except ValueError:
        return None
    if month == 12 and now.month == 1:
        return dt.date(now.year - 1, month, day)
    if month == 1 and now.month == 12:
        return dt.date(now.year + 1, month, day)
    return due


def format_short_reminder_date(value: dt.date) -> str:
    weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    return f"{weekdays[value.weekday()]} {value.day}.{value.month}"


def list_recent_mail(token: str) -> list[dict[str, str]]:
    query = urllib.parse.urlencode({"q": "newer_than:2d -category:promotions -from:me", "maxResults": "25"})
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


def list_delivery_mail(
    token: str, sender_email: str, recipient_email: str, now: dt.datetime
) -> list[dict[str, Any]]:
    messages = search_delivery_message_refs(token)
    delivery_messages = []
    completed_topics = load_completed_delivery_topics() + list_completed_delivery_mail_topics(
        token, sender_email, recipient_email
    )
    for item in messages:
        message = request_json(f"{GMAIL_API}/messages/{item['id']}?format=full", token=token)
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(ohne Betreff)")
        sender = headers.get("from", "")
        if delivery_detection.is_own_delivery_sender(sender, sender_email, recipient_email):
            continue
        text = extract_message_text(message.get("payload", {}))
        snippet = message.get("snippet", "")
        delivery_messages.append(
            {
                "from": sender,
                "subject": subject,
                "date": headers.get("date", ""),
                "snippet": snippet,
                "body": text,
                "thread_id": message.get("threadId", ""),
                "sort_key": int(message.get("internalDate", "0")),
            }
        )
    return delivery_detection.detect_open_deliveries(
        delivery_messages,
        now,
        completed_topics=completed_topics,
    )


def search_delivery_message_refs(token: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query_text in delivery_detection.delivery_search_queries():
        query = urllib.parse.urlencode(
            {"q": query_text, "maxResults": str(delivery_detection.DELIVERY_SEARCH_MAX_RESULTS_PER_QUERY)}
        )
        payload = request_json(f"{GMAIL_API}/messages?{query}", token=token)
        for item in payload.get("messages", []):
            message_id = str(item.get("id") or "")
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            messages.append(item)
            if len(messages) >= delivery_detection.DELIVERY_SEARCH_TOTAL_LIMIT:
                return messages
    return messages


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


def list_waiting_for_mail(token: str, sender_email: str, recipient_email: str, timezone: str) -> list[dict[str, str]]:
    query = urllib.parse.urlencode({"q": "in:sent newer_than:7d -in:trash", "maxResults": "50"})
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
        to_header = headers.get("to", "")
        if is_own_daily_cody_mail(subject, to_header, recipient_email):
            continue
        if delivery_detection.is_suppressed_topic(
            subject,
            snippet,
            completed_topics=load_completed_delivery_topics(),
        ):
            continue
        if not looks_waiting_for_reply(subject, snippet):
            continue
        if looks_like_commerce_status(subject, snippet):
            continue
        sent_at_ms = int(message.get("internalDate", "0"))
        if thread_has_later_external_reply(token, thread_id, sent_at_ms, sender_email):
            continue
        seen_threads.add(thread_id)
        sent_at = dt.datetime.fromtimestamp(sent_at_ms / 1000, tz=ZoneInfo(timezone))
        output.append(
            {
                "source": "gmail_sent",
                "to": to_header,
                "subject": subject,
                "date": headers.get("date", ""),
                "sent_local": sent_at.strftime("%d.%m. %H:%M"),
                "message_id": item["id"],
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
                decoded = preserve_html_links(decoded)
                decoded = re.sub(r"<br\s*/?>", "\n", decoded, flags=re.I)
                decoded = re.sub(r"</p\s*>", "\n", decoded, flags=re.I)
                decoded = re.sub(r"<[^>]+>", " ", decoded)
            chunks.append(html.unescape(decoded))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    return " ".join(" ".join(chunks).split())


def preserve_html_links(value: str) -> str:
    def replace_link(match: re.Match[str]) -> str:
        href = html.unescape(match.group(1)).strip()
        label = clean_mail_excerpt(re.sub(r"<[^>]+>", " ", match.group(2)))
        if not href.startswith(("http://", "https://")):
            return label
        if label:
            return f"{label} {href}"
        return href

    return re.sub(
        r"<a\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        replace_link,
        value,
        flags=re.I | re.S,
    )


def list_completed_delivery_mail_topics(token: str, sender_email: str, recipient_email: str) -> list[str]:
    cody_addresses = delivery_detection.delivery_completion_addresses(sender_email, recipient_email)
    cody_query_terms = " OR ".join(f'"{address}"' for address in sorted(cody_addresses))
    cody_filter = f"(cody OR {cody_query_terms})" if cody_query_terms else "cody"
    query_text = (
        "newer_than:180d in:anywhere "
        f"{cody_filter} "
        "(lieferung OR paket OR bestellung OR sendung OR angekommen OR erhalten OR erledigt OR zugestellt OR geliefert)"
    )
    query = urllib.parse.urlencode({"q": query_text, "maxResults": "100"})
    messages = request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages", [])
    topics: list[str] = []
    for item in messages[:100]:
        message = request_json(
            f"{GMAIL_API}/messages/{item['id']}?format=full",
            token=token,
        )
        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        if "the daily cody" in headers.get("subject", "").lower():
            continue
        participants = " ".join(
            [
                headers.get("from", ""),
                headers.get("to", ""),
                headers.get("cc", ""),
                headers.get("bcc", ""),
            ]
        )
        if cody_addresses and not delivery_detection.message_references_any_address(participants, cody_addresses):
            continue
        message_parts = [
            headers.get("subject", ""),
            message.get("snippet", ""),
            extract_message_text(message.get("payload", {})),
        ]
        for topic in delivery_detection.extract_completed_delivery_topics_from_text("\n".join(message_parts)):
            if topic not in topics:
                topics.append(topic)
    return topics


def load_completed_delivery_topics() -> list[str]:
    if not DELIVERY_STATUS_PATH.exists():
        return []
    try:
        data = json.loads(DELIVERY_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Delivery status unavailable: {exc}", file=sys.stderr)
        return []
    completed = data.get("completed", [])
    return [str(item) for item in completed if str(item).strip()] if isinstance(completed, list) else []


def load_resolved_topic_overrides() -> list[dict[str, Any]]:
    if not RESOLVED_TOPICS_PATH.exists():
        return []
    try:
        data = json.loads(RESOLVED_TOPICS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Resolved topics unavailable: {exc}", file=sys.stderr)
        return []
    completed = data.get("completed", [])
    if not isinstance(completed, list):
        return []

    overrides = []
    for item in completed:
        if isinstance(item, dict):
            topic = str(item.get("topic") or item.get("subject") or item.get("title") or "").strip()
            aliases = item.get("aliases", [])
            reason = str(item.get("note") or item.get("reason") or "Als erledigt markiert.").strip()
        else:
            topic = str(item).strip()
            aliases = []
            reason = "Als erledigt markiert."
        if not topic:
            continue
        if not isinstance(aliases, list):
            aliases = []
        overrides.append(
            {
                "topic": topic,
                "action": "closed",
                "reason": reason,
                "aliases": [str(alias) for alias in aliases if str(alias).strip()],
            }
        )
    return overrides


def infer_resolved_action_overrides(recent_mail: list[dict[str, str]]) -> list[dict[str, Any]]:
    overrides = []
    for item in recent_mail:
        text = normalize_status_text(json.dumps(item, ensure_ascii=False))
        if is_resolved_euromaster_reply(text):
            overrides.append(
                {
                    "topic": "Euromaster Reifen-Auslagerung",
                    "action": "closed",
                    "reason": "Mailantwort: Räder sind vorbereitet und können am 29.06.2026 abgeholt werden.",
                    "aliases": [
                        "Euromaster",
                        "Michael Meyer",
                        "Reifen-Auslagerung",
                        "Räder",
                        "Raeder",
                        "Winterreifen",
                    ],
                }
            )
            break
    return overrides


def is_resolved_euromaster_reply(text: str) -> bool:
    if "euromaster" not in text and "michael meyer" not in text:
        return False
    has_pickup_signal = any(marker in text for marker in ("abholen", "abholung", "abholtermin"))
    has_ready_signal = any(marker in text for marker in ("vorbereitet", "wie geplant", "29 06 26", "29 06 2026"))
    has_wheel_signal = any(marker in text for marker in ("räder", "raeder", "reifen", "winterreifen"))
    return has_pickup_signal and has_ready_signal and has_wheel_signal


def normalize_status_text(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"[\u2010-\u2015‑–—−-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = re.sub(r"[\u2010-\u2015‑–—−-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9äöüß]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def clean_mail_excerpt(value: str) -> str:
    cleaned = html.unescape(value or "")
    cleaned = re.sub(r"[\u034f\u200b-\u200f\u202a-\u202e]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


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


def looks_like_waiting_reminder(reminder: dict[str, str]) -> bool:
    text = f"{reminder.get('title', '')} {reminder.get('notes', '')}".lower()
    markers = (
        "rückmeldung",
        "rueckmeldung",
        "antwort",
        "feedback",
        "nachfassen",
        "nachfragen",
        "da?",
        "winterreifen",
    )
    return any(marker in text for marker in markers)


def is_own_daily_cody_mail(subject: str, to_header: str, recipient_email: str) -> bool:
    return "the daily cody" in subject.lower() and recipient_email.lower() in to_header.lower()


def looks_like_commerce_status(subject: str, snippet: str) -> bool:
    text = f"{subject} {snippet}".lower()
    markers = (
        "amazon",
        "rückgabe",
        "rueckgabe",
        "retoure",
        "rücksendung",
        "ruecksendung",
        "erstattung",
        "bestellung",
        "lieferung",
        "sendung",
        "paket",
        "rechnung",
    )
    return any(marker in text for marker in markers)


def already_sent_today(config: Config, token: str, subject: str) -> bool:
    if config.allow_duplicate:
        return False
    query = urllib.parse.urlencode({"q": f'from:me to:{config.recipient} subject:"{subject}" newer_than:2d'})
    return bool(request_json(f"{GMAIL_API}/messages?{query}", token=token).get("messages"))


def build_briefing(
    config: Config,
    now: dt.datetime,
    weather: dict[str, Any],
    today_events: list[dict[str, Any]],
    upcoming_events: list[dict[str, Any]],
    world_cup_games: list[dict[str, str]],
    reminders: list[dict[str, str]],
    reminders_warning: str | None,
    application_wiki: dict[str, Any],
    recent_mail: list[dict[str, str]],
    delivery_mail: list[dict[str, Any]],
    open_mail: list[dict[str, str]],
    waiting_for_mail: list[dict[str, str]],
) -> str:
    today_reminders, upcoming_reminders, waiting_reminders = split_reminders_for_briefing(reminders, now)
    action_overrides = (
        application_wiki.get("action_overrides", [])
        + load_resolved_topic_overrides()
        + infer_resolved_action_overrides(recent_mail)
    )
    today_reminders = filter_items_by_action_overrides(today_reminders, action_overrides)
    upcoming_reminders = filter_items_by_action_overrides(upcoming_reminders, action_overrides)
    waiting_reminders = filter_items_by_action_overrides(waiting_reminders, action_overrides)
    open_mail = filter_items_by_action_overrides(open_mail, action_overrides)
    waiting_for_mail = filter_items_by_action_overrides(waiting_for_mail, action_overrides)
    application_waiting = filter_items_by_action_overrides(
        format_application_waiting_items(application_wiki), action_overrides
    )
    waiting_for_items = merge_waiting_for_items(
        waiting_for_mail + format_waiting_reminders(waiting_reminders) + application_waiting
    )
    context = {
        "date": format_long_german_date(now),
        "morning_quote": daily_morning_quote(now),
        "reminders_mode": "weekly_planning_full_list" if now.weekday() == 4 else "today_plus_two_days",
        "personal_context": {
            "Pia": "Pia beziehungsweise Pia-Lotta ist Christians Tochter.",
        },
        "weather": weather,
        "today_events": filter_world_cup_events_from_calendar(today_events, world_cup_games),
        "world_cup_games": world_cup_games,
        "upcoming_events": upcoming_events,
        "reminders": upcoming_reminders,
        "today_todos": today_reminders,
        "data_warnings": [reminders_warning] if reminders_warning else [],
        "deliveries": delivery_mail,
        "yesterday_open_mail": open_mail,
        "waiting_for": waiting_for_items,
        "application_wiki": compact_application_wiki_context(application_wiki),
    }
    if config.openai_api_key:
        max_attempts = max(1, config.openai_max_attempts)
        for attempt in range(1, max_attempts + 1):
            try:
                return finalize_briefing(build_ai_briefing(config, context), world_cup_games, delivery_mail)
            except urllib.error.HTTPError as exc:
                if exc.code not in {400, 401, 403, 429} and exc.code < 500:
                    raise
                if exc.code >= 500 and attempt < max_attempts:
                    print(
                        f"OpenAI briefing failed with HTTP {exc.code}; retrying.",
                        file=sys.stderr,
                    )
                    continue
                if not config.allow_template_fallback:
                    raise RuntimeError(
                        f"OpenAI briefing failed with HTTP {exc.code}; no email sent. "
                        "Set ALLOW_TEMPLATE_FALLBACK=true to send the local template instead."
                    ) from exc
                print(
                    f"OpenAI briefing failed with HTTP {exc.code}; using current-data template briefing.",
                    file=sys.stderr,
                )
                return finalize_briefing(build_template_briefing(context), world_cup_games, delivery_mail)
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                if attempt < max_attempts:
                    print(f"OpenAI briefing unavailable ({exc}); retrying.", file=sys.stderr)
                    continue
                if not config.allow_template_fallback:
                    raise RuntimeError(
                        "OpenAI briefing unavailable after "
                        f"{max_attempts} attempts ({exc}); no email sent. "
                        "Set ALLOW_TEMPLATE_FALLBACK=true to send the local template instead."
                    ) from exc
                print(
                    f"OpenAI briefing unavailable after {max_attempts} attempts ({exc}); "
                    "using current-data template briefing.",
                    file=sys.stderr,
                )
                return finalize_briefing(build_template_briefing(context), world_cup_games, delivery_mail)
    return finalize_briefing(build_template_briefing(context), world_cup_games, delivery_mail)


def build_ai_context(context: dict[str, Any]) -> dict[str, Any]:
    ai_context = dict(context)
    weather = dict(ai_context.get("weather") or {})
    weather.pop("summary", None)
    ai_context["weather"] = weather
    return ai_context


def build_ai_briefing(config: Config, context: dict[str, Any]) -> str:
    system = (
        "Du bist Cody, Christians persönliche Morgenmail. Du bist die erste Mail seines Tages: "
        "wie jemand aus der Familie, der am Küchentisch kurz sortiert, was heute anliegt. "
        "Schreib warm, ruhig, vertraut und klar. Ein bisschen trockener Humor ist okay, aber nur wenn er natürlich wirkt. "
        "Keine sterile Assistenten-Sprache, keine Wetter-App-Sprache, keine Management-Floskeln, keine bemühten Running Gags. "
        "Schreibe ein kompaktes deutsches Daily Briefing nach dem Daily-Dover-Muster, aber persönlicher und lesbarer. "
        "Nutze diese Markdown-Struktur: H1-Titel, ein einziges kursives Zitat mit Autor, dann H2-Abschnitte "
        "'Today', 'Today's to-dos', optional 'Reminders' nur wenn Daten vorhanden sind, "
        "'Waiting for...', 'Deliveries' und 'Approaching'. "
        "Der kursive Satz direkt unter dem Titel muss exakt das im Kontext gelieferte morning_quote sein. "
        "Keine Aufgaben, Termine, Wetterdaten oder Erinnerungen in diese Zeile schreiben. "
        "Das Zitat ist keine Affirmation und kein Coaching-Spruch; ändere daran nichts und erfinde keinen Ersatz. "
        "Alles außer Titel und Einleitung muss als kurze Bulletpoints erscheinen. "
        "Kein langer Brief, keine Begrüßung mit Leerzeilen, keine horizontalen Trennstriche, keine Tabellen. "
        "Today ist der Tagesüberblick: Wetter, Termine, Dinge die heute passieren. "
        "Wenn data_warnings vorhanden sind, nenne sie unter Today als knappen Hinweis und formuliere sie nicht als Aufgabe. "
        "Wenn world_cup_games vorhanden ist: Unter Today jedes WM-Spiel als eigene kurze Zeile nennen, "
        "mit Anstoßzeit und Free-TV-Sender aus free_tv. "
        "Wenn free_tv 'nicht bei ARD/ZDF gefunden' ist, schreibe nicht, dass es im Free-TV läuft. "
        "Today's to-dos ist die Aktionsliste: alle today_todos aus Apple Reminders plus offene Mails vom Vortag. "
        "Formuliere To-dos knapp, freundlich und konkret; lieber natürlich als pointiert. "
        "Unter Reminders: Apple Erinnerungen aus dem lokalen Export, knapp mit Fälligkeitsdatum; "
        "Reminders ist nur der Ausblick, today_todos dort nicht wiederholen. "
        "die Liste nur nennen, wenn sie wirklich vorhanden ist. Niemals 'keine Angabe' schreiben. "
        "An Freitagen dürfen Reminders als Wochenplanungsblick länger sein, sonst sehr knapp halten. "
        "Packe Wetter unter Today als eine persönliche Cody-Zeile, höchstens zwei kurze Wetter-Bullets wenn zusätzlich eine Warnung wichtig ist. "
        "Formuliere Wetter aus weather.today, weather.next_days und weather.warnings selbst: natürlich, intelligent, ohne Rohdaten-Litanei. "
        "Schreibe nicht im Muster 'Hamburg: gerade X, später Y bis Z' und nicht im Wetter-App-Stil 'Am Nachmittag N % Regenwahrscheinlichkeit'. "
        "Nenne nur Zahlen, die für die Entscheidung des Tages wirklich helfen, zum Beispiel Hitze, Regenfenster, Warnung oder starke Böen; runde Temperaturen auf ganze Grad. "
        "Bei Wetterwarnungen: klar sagen, was relevant ist und was Christian praktisch tun sollte, aber ohne Alarmismus und ohne Helden-/Mittagssonnen-Floskeln. "
        "Keine Witze über Hamburg, kein 'Hamburg lacht zuletzt', keine Wiederholungsphrase. "
        "Wenn Pia oder Pia-Lotta auftaucht: Das ist Christians Tochter. Schreib warm und schlicht, nicht wie ein Kontakt-Ping. "
        "Unter Deliveries: ausschließlich Einträge aus deliveries verwenden; keine Lieferungen aus recent_mail, "
        "alten Daily-Cody-Mails oder sonstigem Kontext rekonstruieren. Offene Bestellungen und Lieferungen "
        "aller Händler, zum Beispiel Amazon, Proraso oder Comics, mit Liefertermin und Trackinglink, falls vorhanden. "
        "Keine gekürzten oder abgebrochenen Artikelnamen nennen; wenn nur ein abgeschnittener Produktname vorliegt, "
        "lieber die beste Produktkategorie oder Händler plus Bestellung schreiben. Niemals erklären, dass ein Artikelname abgeschnitten ist. "
        "Tracking-URLs nie ausschreiben; der Linktext muss exakt 'Trackinglink' sein. "
        "Unter Waiting for... ausschließlich Einträge aus waiting_for verwenden; application_wiki ist der kuratierte Bewerbungs-Dashboard-Kontext. "
        "Wenn application_wiki oder action_overrides sagen, dass ein Bewerbungskanal nicht aktiv nachverfolgt werden soll, "
        "darf daraus kein To-do und keine Rückruf-Erinnerung entstehen. "
        "Wenn source=gmail_sent: echte gesendete Gmail, nutze subject, to und sent_local. "
        "Wenn source=reminder: als Nachfass-Erinnerung formulieren, nicht als gesendete Mail. "
        "Wenn source=application_wiki: als Bewerbungs-/Wartepunkt formulieren und die nächste Aktion aus next_action beachten. "
        "Behaupte nie, Christian habe eine Antwort oder Mail gesendet, wenn source nicht gmail_sent ist. "
        "Daily-Cody-Mails an Christian selbst niemals in Waiting for aufnehmen. "
        "Amazon, Lieferungen, Rückgaben und Bestellungen gehören nicht in Waiting for..., sondern höchstens in Deliveries. "
        "Erledigte oder unterdrückte Themen nicht erwähnen; insbesondere kein 200W-USB-C-Thema. "
        "Unter Today's to-dos: offene Mails vom Vortag, auf die Christian "
        "wahrscheinlich reagieren sollte, jeweils mit einem sehr kurzen Antwortentwurf. "
        "Sei nützlich, konkret, freundlich, und erfinde keine Fakten."
    )
    user = "Nutze diese Daten und schreibe die E-Mail als Markdown:\n\n" + json.dumps(
        build_ai_context(context), ensure_ascii=False, indent=2
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for style_attempt in range(2):
        body = {
            "model": config.openai_model,
            "messages": messages,
        }
        if not config.openai_model.startswith("gpt-5"):
            body["temperature"] = 0.4
        response = request_json(
            OPENAI_API,
            method="POST",
            token=config.openai_api_key,
            body=body,
            timeout_seconds=config.openai_timeout_seconds,
        )
        briefing = response["choices"][0]["message"]["content"].strip()
        if not has_weather_style_problem(briefing) or style_attempt == 1:
            return briefing
        messages = [
            *messages,
            {"role": "assistant", "content": briefing},
            {
                "role": "user",
                "content": (
                    "Schreibe die komplette Mail neu. Die Wetterpassage klingt noch wie eine "
                    "vorformulierte Wetter-App-Zeile. Keine Konstruktion wie 'Hamburg: gerade...', "
                    "keine 'Regenwahrscheinlichkeit'-Formulierung, keine Helden-/Mittagssonnen-Floskel, "
                    "keine 'nasse Kanten'. Formuliere nur eine natürliche, hilfreiche Einschätzung."
                ),
            },
        ]
    raise RuntimeError("OpenAI briefing generation returned no content.")


def has_weather_style_problem(briefing: str) -> bool:
    normalized = normalize_search_text(briefing)
    banned_phrases = (
        "nasse kanten",
        "nicht den helden",
        "helden in der mittagssonne",
        "regenwahrscheinlichkeit",
    )
    if any(phrase in normalized for phrase in banned_phrases):
        return True
    return bool(re.search(r"hamburg\s+gerade\s+\d", normalized))


def build_template_briefing(context: dict[str, Any]) -> str:
    weather = context["weather"]
    lines = [
        f"# Daily Cody — {context['date']}",
        "",
        context["morning_quote"],
        "",
        "## Today",
        f"- {weather['summary']}",
    ]
    lines.extend(format_data_warning_items(context.get("data_warnings", [])))
    lines.extend(format_items(context["today_events"], "Heute steht nichts Kritisches im Kalender."))
    lines.extend(format_world_cup_game_items(context["world_cup_games"]))
    lines.extend(["", "## Today's to-dos"])
    todo_lines = format_today_todo_reminders(context["today_todos"])
    todo_lines.extend(format_open_mail_items(context["yesterday_open_mail"]))
    lines.extend(todo_lines or ["- Nichts Dringendes offen — schöner kleiner Bonus für heute."])
    if context["reminders"]:
        lines.extend(["", "## Reminders"])
        lines.extend(format_reminder_items(context["reminders"]))
    lines.extend(["", "## Waiting for..."])
    lines.extend(format_waiting_for_items(context["waiting_for"]))
    lines.extend(["", "## Deliveries"])
    lines.extend(format_delivery_items(context["deliveries"]))
    lines.extend(["", "## Approaching"])
    lines.extend(format_items(context["upcoming_events"], "Keine nahen Termine gefunden."))
    return "\n".join(lines)


def ensure_world_cup_lines(briefing: str, world_cup_games: list[dict[str, str]]) -> str:
    if not world_cup_games:
        return briefing
    existing = normalize_search_text(briefing)
    missing_lines = []
    for item, line in zip(world_cup_games, format_world_cup_game_items(world_cup_games), strict=False):
        fixture = normalize_search_text(item.get("fixture", ""))
        if fixture and fixture in existing:
            continue
        missing_lines.append(line)
    if not missing_lines:
        return briefing

    lines = briefing.splitlines()
    today_index = next((idx for idx, line in enumerate(lines) if line.strip() == "## Today"), None)
    if today_index is None:
        return briefing.rstrip() + "\n\n## Today\n" + "\n".join(missing_lines)

    insert_at = today_index + 1
    while insert_at < len(lines) and not lines[insert_at].strip():
        insert_at += 1
    if insert_at < len(lines) and lines[insert_at].lstrip().startswith("- "):
        insert_at += 1
    lines[insert_at:insert_at] = missing_lines
    return "\n".join(lines)


def finalize_briefing(
    briefing: str,
    world_cup_games: list[dict[str, str]],
    deliveries: list[dict[str, Any]],
) -> str:
    briefing = ensure_world_cup_lines(briefing, world_cup_games)
    briefing = replace_delivery_section(briefing, deliveries)
    return normalize_trackinglink_labels(briefing)


def replace_delivery_section(briefing: str, deliveries: list[dict[str, Any]]) -> str:
    replacement = ["## Deliveries", *format_delivery_items(deliveries), ""]
    lines = briefing.splitlines()
    start = next((idx for idx, line in enumerate(lines) if is_delivery_heading(line)), None)
    if start is None:
        return briefing.rstrip() + "\n\n" + "\n".join(replacement)

    end = start + 1
    while end < len(lines) and not is_markdown_heading(lines[end]):
        end += 1
    lines[start:end] = replacement
    return "\n".join(lines)


def is_delivery_heading(line: str) -> bool:
    label = normalize_search_text(line)
    return label in {"deliveries", "delivery"} or label.endswith(" deliveries")


def is_markdown_heading(line: str) -> bool:
    return bool(re.match(r"^\s*#{1,6}\s+\S", line))


def normalize_trackinglink_labels(markdown: str) -> str:
    def replace_markdown_link(match: re.Match[str]) -> str:
        url = match.group(2)
        if not is_tracking_url(url):
            return match.group(0)
        return f"[Trackinglink]({url})"

    markdown = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", replace_markdown_link, markdown)

    def replace_bare_link(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,;")
        suffix = match.group(0)[len(url) :]
        if not is_tracking_url(url):
            return match.group(0)
        return f"[Trackinglink]({url}){suffix}"

    return re.sub(r"(?<!\]\()https?://[^\s)]+", replace_bare_link, markdown)


def is_tracking_url(url: str) -> bool:
    lowered = url.lower()
    markers = (
        "track",
        "tracking",
        "sendung",
        "liefer",
        "dhl",
        "hermes",
        "dpd",
        "ups",
        "gls",
        "custcomm",
        "post",
    )
    return any(marker in lowered for marker in markers)


def daily_morning_quote(now: dt.datetime) -> str:
    quotes = load_morning_quotes()
    text, author = quotes[(now.toordinal() * 17) % len(quotes)]
    return f"„{text}“ — {author}"


def load_morning_quotes() -> list[tuple[str, str]]:
    fallback = [
        (
            "Man muss das Wahre immer wiederholen, weil auch der Irrtum um uns her immer wieder gepredigt wird.",
            "Johann Wolfgang von Goethe",
        )
    ]
    if not MORNING_QUOTES_PATH.exists():
        return fallback
    try:
        data = json.loads(MORNING_QUOTES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Morning quotes unavailable: {exc}", file=sys.stderr)
        return fallback
    if not isinstance(data, list):
        return fallback
    quotes = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        author = str(item.get("author") or "").strip()
        if text and author:
            quotes.append((text, author))
    return quotes or fallback


def format_items(items: list[dict[str, Any]], empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    return [format_calendar_item(item) for item in items[:12]]


def format_data_warning_items(warnings: list[str]) -> list[str]:
    return [f"- Hinweis: {warning}" for warning in warnings[:3] if warning]


def format_calendar_item(item: dict[str, Any]) -> str:
    time_text = format_time(item.get("start", ""))
    summary = format_event_summary(str(item.get("summary", "")))
    calendar = str(item.get("calendar", "")).strip()
    if "geburt" in calendar.lower() or "geburtstag" in summary.lower():
        return f"- {time_text} {summary}".strip()
    suffix = f" ({calendar})" if calendar else ""
    return f"- {time_text} {summary}{suffix}".strip()


def format_event_summary(summary: str) -> str:
    if re.search(r"\bpia(?:-lotta)?\b", summary, flags=re.I) and "geburtstag" in summary.lower():
        return "Pia hat Geburtstag."
    return summary


def format_world_cup_game_items(items: list[dict[str, str]]) -> list[str]:
    lines = []
    for item in items[:8]:
        kickoff = item.get("kickoff") or format_time(item.get("start", "")) or "Anstoßzeit offen"
        sender = item.get("free_tv") or "nicht bei ARD/ZDF gefunden"
        if sender == "nicht bei ARD/ZDF gefunden":
            sender_text = "kein ARD/ZDF-Free-TV-Hinweis gefunden"
        else:
            sender_text = f"Free-TV: {sender}"
        lines.append(f"- WM: {kickoff} {item.get('fixture', 'Spiel offen')} — {sender_text}.")
    return lines


def format_mail_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- Keine auffälligen neuen Mails gefunden."]
    return [f"- Mail prüfen: {item['subject']} — {item['from']}" for item in items[:8]]


def format_delivery_items(items: list[dict[str, Any]]) -> list[str]:
    open_items = [item for item in items if item.get("status") != "delivered"]
    if not open_items:
        return ["- Keine offenen Liefer- oder Bestellmails gefunden."]
    lines = []
    for item in open_items[:8]:
        link = f" — [Trackinglink]({item['tracking_links'][0]})" if item.get("tracking_links") else ""
        lines.append(f"- {item['subject']} — {item['snippet']}{link}")
    return lines


def format_waiting_for_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return ["- Keine offenen Nachfasspunkte gefunden."]
    lines = []
    for item in items[:8]:
        if item.get("source") == "reminder":
            due = f"{item['due']} — " if item.get("due") else ""
            lines.append(f"- {due}{item['subject']} — nachfassen.")
        elif item.get("source") == "application_wiki":
            since = f"seit {item['since']}: " if item.get("since") else ""
            snippet = item.get("snippet", "")
            next_action = item.get("next_action", "")
            if next_action and normalize_status_text(next_action) not in normalize_status_text(snippet):
                snippet = f"{snippet} {next_action}".strip()
            lines.append(f"- {item['subject']} — {since}{snippet}".strip())
        else:
            lines.append(
                f"- {item['subject']} — an {item['to']}, gesendet {item.get('sent_local') or item.get('date', '')}; {item['snippet']}"
            )
    return lines


def format_reminder_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return []
    lines = []
    for item in items[:10]:
        due = f"{item['due']}: " if item.get("due") else ""
        list_name = f" ({item['list']})" if item.get("list") else ""
        notes = f" — {item['notes']}" if item.get("notes") else ""
        lines.append(f"- {due}{item['title']}{list_name}{notes}")
    return lines


def format_waiting_reminders(items: list[dict[str, str]]) -> list[dict[str, str]]:
    output = []
    for item in items:
        output.append(
            {
                "source": "reminder",
                "subject": item.get("title", ""),
                "due": item.get("due", ""),
                "snippet": item.get("notes", ""),
            }
        )
    return output


def format_application_waiting_items(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    output = []
    for item in snapshot.get("waiting_for", [])[:10]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or item.get("company") or "").strip()
        detail = str(item.get("detail") or item.get("waiting_for") or item.get("status") or "").strip()
        next_action = str(item.get("next_action") or "").strip()
        if not subject and not detail:
            continue
        output.append(
            {
                "source": "application_wiki",
                "subject": strip_long(subject or "Bewerbungsprozess", 120),
                "since": str(item.get("since") or "").strip(),
                "snippet": strip_long(detail, 260),
                "next_action": strip_long(next_action, 180),
            }
        )
    return output


def compact_application_wiki_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": snapshot.get("source", ""),
        "generated_at": snapshot.get("generated_at", ""),
        "dashboard_updated": snapshot.get("dashboard_updated", ""),
        "items": snapshot.get("items", [])[:12],
        "action_overrides": snapshot.get("action_overrides", [])[:20],
    }


def filter_items_by_action_overrides(
    items: list[dict[str, Any]], action_overrides: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not action_overrides:
        return items
    return [item for item in items if not is_blocked_by_action_override(item, action_overrides)]


def is_blocked_by_action_override(item: dict[str, Any], action_overrides: list[dict[str, Any]]) -> bool:
    haystack = normalize_status_text(json.dumps(item, ensure_ascii=False))
    if not haystack:
        return False
    blocking_actions = {"do_not_follow_up", "closed", "paused", "wait_only"}
    for override in action_overrides:
        if not isinstance(override, dict):
            continue
        if str(override.get("action", "")).strip() not in blocking_actions:
            continue
        terms = [str(override.get("topic", ""))]
        aliases = override.get("aliases", [])
        if isinstance(aliases, list):
            terms.extend(str(alias) for alias in aliases)
        for term in terms:
            normalized = normalize_status_text(term)
            if normalized and len(normalized) >= 4 and normalized in haystack:
                return True
    return False


def merge_waiting_for_items(items: list[dict[str, str]]) -> list[dict[str, str]]:
    merged = []
    seen = set()
    for item in items:
        key = normalize_status_text(item.get("subject") or item.get("to") or item.get("snippet") or "")
        if not key:
            key = normalize_status_text(json.dumps(item, ensure_ascii=False))
        key = key[:80]
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[:12]


def format_today_todo_reminders(items: list[dict[str, str]]) -> list[str]:
    lines = []
    for item in items[:8]:
        notes = f" — {item['notes']}" if item.get("notes") else ""
        lines.append(f"- {item['title']}{notes}")
    return lines


def format_open_mail_items(items: list[dict[str, str]]) -> list[str]:
    if not items:
        return []
    lines = []
    for item in items[:6]:
        lines.append(
            f"- Offen prüfen: {item['subject']} — {item['from']}. Antwortidee: {item['suggested_reply']}"
        )
    return lines


def format_time(value: str, timezone: str = "Europe/Berlin") -> str:
    if not value:
        return ""
    if len(value) == 10:
        try:
            parsed_date = dt.date.fromisoformat(value)
            weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
            return f"{weekdays[parsed_date.weekday()]} {parsed_date.day}.{parsed_date.month}."
        except ValueError:
            return value
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo(timezone))
        return parsed.strftime("%H:%M")
    except ValueError:
        return value


def format_temp(value: Any) -> str:
    if value is None:
        return "keine Daten"
    try:
        return f"{float(value):.1f}".replace(".", ",") + " °C"
    except (TypeError, ValueError):
        return str(value)


def format_percent(value: Any) -> str:
    if value is None:
        return "keine Daten"
    try:
        return f"{round(float(value))} %"
    except (TypeError, ValueError):
        return str(value)


def format_speed(value: Any) -> str:
    if value is None:
        return "keine Daten"
    try:
        return f"{round(float(value))} km/h"
    except (TypeError, ValueError):
        return str(value)


def format_long_german_date(value: dt.datetime) -> str:
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    return f"{weekdays[value.weekday()]}, {value:%d.%m.%Y}"


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


def build_sample_briefing() -> str:
    zone = ZoneInfo("Europe/Berlin")
    now = dt.datetime.now(zone)
    config = Config(
        sender="sample@example.com",
        recipient="sample@example.com",
        timezone="Europe/Berlin",
        calendar_names=["MixedCup2026"],
        weather_latitude="53.4439",
        weather_longitude="9.9857",
        weather_label="21077 Hamburg",
        google_client_id="",
        google_client_secret="",
        google_refresh_token="",
        openai_api_key=None,
        openai_model="gpt-5.5",
        openai_timeout_seconds=120,
        openai_max_attempts=2,
        allow_template_fallback=False,
        send_window_hour=6,
        send_window_end_hour=9,
        force_send=True,
        allow_duplicate=True,
        dry_run=True,
        include_undated_reminders=False,
        require_fresh_reminders=False,
        fail_on_stale_reminders=False,
        reminders_max_age_hours=60,
        reminders_export_path="data/reminders.json",
        refresh_stale_reminders=False,
        reminders_refresh_command="scripts/export_apple_reminders_if_window.sh",
        reminders_refresh_timeout_seconds=180,
        application_wiki_snapshot_path="data/application_wiki_snapshot.json",
    )
    return build_briefing(
        config,
        now,
        {"summary": "Hamburg: Testwetter für den lokalen Probelauf."},
        [],
        [],
        [
            {"fixture": "Mexiko - Südafrika", "kickoff": "21:00", "free_tv": "ZDF"},
            {
                "fixture": "Südkorea - Tschechien",
                "kickoff": "04:00",
                "free_tv": "nicht bei ARD/ZDF gefunden",
            },
        ],
        [],
        None,
        {},
        [],
        [],
        [],
        [],
    )


def main() -> int:
    if any(arg in {"--sample", "--sample-briefing"} for arg in sys.argv[1:]):
        print(build_sample_briefing())
        return 0

    config = load_config()
    zone = ZoneInfo(config.timezone)
    now = dt.datetime.now(zone)
    if not config.force_send and not (config.send_window_hour <= now.hour < config.send_window_end_hour):
        print(
            f"Not send window in {config.timezone}: now={now.isoformat()}, "
            f"window={config.send_window_hour}:00-{config.send_window_end_hour}:00"
        )
        return 0

    token = refresh_google_token(config)
    subject = f"The Daily Cody — {now:%Y-%m-%d}"
    if already_sent_today(config, token, subject):
        print(f"Already sent: {subject}")
        return 0

    weather = get_weather(config)
    today_events, upcoming_events = list_calendar_events(config, token, now)
    world_cup_games = list_world_cup_games(config, now, today_events)
    reminders, reminders_warning = read_exported_reminders(config, now)
    application_wiki = read_application_wiki_snapshot(config)
    recent_mail = list_recent_mail(token)
    delivery_mail = list_delivery_mail(token, config.sender, config.recipient, now)
    open_mail = list_yesterday_open_mail(token, now)
    waiting_for_mail = list_waiting_for_mail(token, config.sender, config.recipient, config.timezone)
    briefing = build_briefing(
        config,
        now,
        weather,
        today_events,
        upcoming_events,
        world_cup_games,
        reminders,
        reminders_warning,
        application_wiki,
        recent_mail,
        delivery_mail,
        open_mail,
        waiting_for_mail,
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
