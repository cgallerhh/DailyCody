"""Task normalization and fresh read-only official TickTick MCP task source.

Transport: https://mcp.ticktick.com/; only the official MCP read tools.
Superseded Open API coverage fixtures live under tests, outside production.
No snapshots, task mutations or ChatGPT connector-token lookup.
"""
from __future__ import annotations

import datetime as dt
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BERLIN = ZoneInfo("Europe/Berlin")
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class TickTickError(RuntimeError):
    """Only fixed, non-sensitive descriptions may leave this adapter."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the Authorization header to a redirect destination.
        return None


@dataclass
class TaskRead:
    tasks: list[dict[str, str]] = field(default_factory=list)
    warning: str | None = None
    fetched_at: str = ""
    project_count: int = 0
    open_task_count: int = 0
    inbox_probe_verified: bool = False

    def status(self) -> dict[str, Any]:
        return {"source": "TickTick", "fetched_at": self.fetched_at,
                "project_count": self.project_count, "open_task_count": self.open_task_count,
                "available": self.warning is None}



def project_data_shape(data: Any) -> dict[str, Any]:
    """Allowlisted types/counts only; never keys, IDs, names or response values."""
    def kind(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)):
            return "number"
        return "string" if isinstance(value, str) else "other"

    result: dict[str, Any] = {"body_type": kind(data)}
    if isinstance(data, dict):
        for field_name in ("project", "tasks", "columns"):
            result[field_name + "_type"] = kind(data[field_name]) if field_name in data else "missing"
        result["project_id_present"] = isinstance(data.get("project"), dict) and isinstance(data["project"].get("id"), str)
        result["task_count"] = len(data["tasks"]) if isinstance(data.get("tasks"), list) else None
    return result


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise TickTickError("TickTick hat eine ungültige stabile ID geliefert.")
    return value


def task_date(value: Any, *, all_day: bool, timezone: str) -> tuple[str, str, str]:
    if value in (None, ""):
        return "", "", ""
    if not isinstance(value, str):
        raise TickTickError("TickTick hat ein ungültiges Aufgabendatum geliefert.")
    try:
        zone = ZoneInfo(timezone or "Europe/Berlin")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date = dt.date.fromisoformat(value)
            instant = None
        else:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                first, second = parsed.replace(tzinfo=zone, fold=0), parsed.replace(tzinfo=zone, fold=1)
                # A timed task in an ambiguous/nonexistent DST hour needs an offset.
                if not all_day and (first.utcoffset() != second.utcoffset() or
                        first.astimezone(dt.UTC).astimezone(zone).replace(tzinfo=None) != parsed):
                    raise ValueError("ambiguous local time")
                parsed = first
            date = parsed.astimezone(zone if all_day else BERLIN).date()
            instant = None if all_day else parsed.astimezone(BERLIN)
    except (ValueError, ZoneInfoNotFoundError):
        raise TickTickError("TickTick hat ein ungültiges oder mehrdeutiges Aufgabendatum geliefert.") from None
    label = f"{['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'][date.weekday()]} {date.day}.{date.month}.{date.year}"
    label += f" {instant:%H:%M} (Berlin)" if instant else " (ganztägig)"
    return date.isoformat(), instant.isoformat() if instant else "", label


def is_waiting(raw: dict[str, Any], list_name: str) -> bool:
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        raise TickTickError("TickTick hat ungültige Aufgaben-Tags geliefert.")
    text = " ".join([list_name, str(raw.get("title") or ""), str(raw.get("content") or ""),
                     str(raw.get("desc") or ""), *map(str, tags)]).casefold()
    return bool(re.search(r"\b(warten[\s_-]*auf|warte auf|waiting(?:[\s_-]*for)?|rückmeldung|rueckmeldung|nachfassen|nachfragen|feedback)\b", text))


def normalize_task(raw: dict[str, Any], list_name: str) -> dict[str, str] | None:
    status = raw.get("status")
    if status in (2, -1) or raw.get("kind") == "NOTE":
        return None
    if type(status) is not int or status != 0:
        raise TickTickError("TickTick-Aufgabenstatus ist nicht verlässlich.")
    # status=0 is authoritative: reopened/recurring tasks may retain completedTime.
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise TickTickError("TickTick-Aufgabentitel fehlt.")
    if raw.get("isAllDay") is not None and type(raw["isAllDay"]) is not bool:
        raise TickTickError("TickTick hat einen ungültigen Ganztagsstatus geliefert.")
    all_day = raw.get("isAllDay") is True
    zone = raw.get("timeZone") or "Europe/Berlin"
    if not isinstance(zone, str):
        raise TickTickError("TickTick hat eine ungültige Aufgabenzeitzone geliefert.")
    due_date, due_at, due = task_date(raw.get("dueDate"), all_day=all_day, timezone=zone)
    start_date, start_at, start = task_date(raw.get("startDate"), all_day=all_day, timezone=zone)
    task_id, project_id = identifier(raw.get("id")), identifier(raw.get("projectId"))
    notes = str(raw.get("content") or raw.get("desc") or "").strip()
    return {"source": "ticktick", "id": task_id, "project_id": project_id,
            "title": title.strip(), "list": list_name, "notes": notes,
            "due_date": due_date, "due_at": due_at, "due": due,
            "start_date": start_date, "start_at": start_at, "start": start,
            "waiting": "true" if is_waiting(raw, list_name) else "false",
            "source_url": f"https://ticktick.com/webapp/#p/{project_id}/tasks/{task_id}"}


def relevant(task: dict[str, str], now: dt.datetime) -> bool:
    date = task["due_date"] or task["start_date"]
    today = now.astimezone(BERLIN).date()
    days = 7 if today.weekday() == 4 else 2
    return task["waiting"] == "true" or not date or dt.date.fromisoformat(date) <= today + dt.timedelta(days=days)


def split_tasks(tasks: list[dict[str, str]], now: dt.datetime) -> tuple[list, list, list]:
    today, later, waiting = [], [], []
    for task in tasks:
        date = task["due_date"] or task["start_date"]
        if task["waiting"] == "true":
            waiting.append(task)
        elif date and dt.date.fromisoformat(date) <= now.astimezone(BERLIN).date():
            today.append(task)
        else:
            later.append(task)
    return today, later, waiting



def read_tasks(auth_json: str, now: dt.datetime, *, timeout_seconds: float = 15,
               total_timeout_seconds: float = 90, required_inbox_task_id: str | None = None) -> TaskRead:
    from ticktick_mcp import MCPError, TaskReader
    from ticktick_mcp_auth import AuthError, EnvironmentStore, Lifecycle
    try:
        token = Lifecycle(EnvironmentStore(auth_json)).access_token()
        return TaskReader(token, timeout_seconds=timeout_seconds,
                          total_timeout_seconds=total_timeout_seconds).read(
                              now, required_inbox_task_id=required_inbox_task_id)
    except (MCPError, AuthError, TickTickError) as exc:
        return TaskRead(warning=f"TickTick-Aufgaben nicht aktuell verfügbar: {exc} "
                        "Aufgabenstand unbekannt; keine gespeicherten Aufgaben verwendet.")
    except (ValueError, TypeError, KeyError, AttributeError):
        return TaskRead(warning="TickTick-MCP-Antwort ist nicht verlässlich. Aufgabenstand unbekannt; "
                        "keine gespeicherten Aufgaben verwendet.")


def format_task(task: dict[str, str]) -> str:
    when = (f"fällig {task['due']}" if task.get("due") else
            f"geplant {task['start']}" if task.get("start") else "ohne Termin")
    # The existing renderer HTML-escapes Markdown; neutralise Markdown in user text.
    def text(value: str) -> str:
        return re.sub(r"[\[\]*`\\]", "", " ".join(value.split()))
    notes = f" — {text(task['notes'])}" if task.get("notes") else ""
    return (f"- {text(task.get('title') or task.get('subject') or '')} ({text(task.get('list', ''))})"
            f" — {when}{notes} [TickTick]({task['source_url']})")
