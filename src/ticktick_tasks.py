"""Read-only TickTick Open API adapter. No snapshots, task writes or token storage.

Contract: https://developer.ticktick.com/docs/openapi.md (checked 2026-10-06).
Project pagination is documented; project data returns the full undone task list.
The date-filter endpoint has a 200-task cap and is deliberately not used.
Full Inbox discovery/read coverage is NOT verified; see docs/ticktick-inbox-blocker.md.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

API = "https://api.ticktick.com/open/v1"
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

    def status(self) -> dict[str, Any]:
        return {"source": "TickTick", "fetched_at": self.fetched_at,
                "project_count": self.project_count, "open_task_count": self.open_task_count,
                "available": self.warning is None}


class TickTickClient:
    def __init__(self, access_token: str, *, timeout_seconds: float = 15,
                 total_timeout_seconds: float = 90, page_size: int = 100,
                 max_pages: int = 100):
        if not access_token or any(c.isspace() for c in access_token):
            raise TickTickError("TickTick-Zugriff ist nicht eingerichtet.")
        if (not math.isfinite(timeout_seconds) or not math.isfinite(total_timeout_seconds) or
                timeout_seconds <= 0 or total_timeout_seconds <= 0 or not 1 <= page_size <= 200 or max_pages < 1):
            raise TickTickError("TickTick-Abrufkonfiguration ist ungültig.")
        self._token = access_token
        self.timeout = timeout_seconds
        self.total_timeout = total_timeout_seconds
        self.page_size = page_size
        self.max_pages = max_pages
        self.opener = urllib.request.build_opener(NoRedirect())
        self.deadline = 0.0

    def _remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TickTickError("TickTick-Abruf hat sein Zeitlimit überschritten.")
        return remaining

    def _get(self, path: str) -> Any:
        request = urllib.request.Request(API + path, method="GET", headers={
            "Authorization": "Bearer " + self._token, "Accept": "application/json",
            "Cache-Control": "no-cache"})
        for attempt in range(3):
            try:
                with self.opener.open(request, timeout=min(self.timeout, self._remaining())) as response:
                    payload = response.read(MAX_RESPONSE_BYTES + 1)
                self._remaining()
                if len(payload) > MAX_RESPONSE_BYTES:
                    raise TickTickError("TickTick-Antwort überschreitet das Sicherheitslimit.")
                return json.loads(payload)
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code in (401, 403):
                    raise TickTickError("TickTick-Autorisierung ist abgelaufen oder unzureichend.") from None
                if code != 429 and not 500 <= code <= 599:
                    raise TickTickError(f"TickTick-Abruf fehlgeschlagen (HTTP {code}).") from None
                failure = "TickTick ist nach begrenzten Wiederholungen nicht erreichbar."
            except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
                failure = "TickTick ist nach begrenzten Wiederholungen nicht erreichbar."
            except (ValueError, UnicodeError, RecursionError):
                raise TickTickError("TickTick hat ungültige JSON-Daten geliefert.") from None
            if attempt == 2:
                raise TickTickError(failure) from None
            delay = 2 ** attempt
            if self._remaining() <= delay:
                raise TickTickError("TickTick-Abruf hat sein Zeitlimit überschritten.") from None
            time.sleep(delay)
        raise AssertionError("unreachable")

    def read(self, now: dt.datetime) -> TaskRead:
        self.deadline = time.monotonic() + self.total_timeout
        projects: dict[str, dict[str, Any]] = {}
        # The Open API enumeration does not establish the MCP's virtual Inbox.
        # Keep a separate Inbox probe and fail if its coverage cannot be verified.
        for page in range(self.max_pages):
            query = urllib.parse.urlencode({"offset": page * self.page_size, "limit": self.page_size})
            records = self._get("/project?" + query)
            if not isinstance(records, list) or len(records) > self.page_size:
                raise TickTickError("TickTick-Listenabdeckung ist nicht verlässlich (Pagination).")
            old_ids = set(projects)
            for project in records:
                if not isinstance(project, dict):
                    raise TickTickError("TickTick hat eine ungültige Liste geliefert.")
                project_id = identifier(project.get("id"))
                if project_id in projects and projects[project_id] != project:
                    raise TickTickError("TickTick-Listen haben sich während des Abrufs geändert.")
                projects[project_id] = project
            if records and set(projects) == old_ids:
                raise TickTickError("TickTick-Pagination wiederholt dieselbe Seite.")
            if len(records) < self.page_size:
                break
        else:
            raise TickTickError("TickTick-Listenabdeckung überschreitet das Seitenlimit.")
        projects.setdefault("inbox", {"id": "inbox", "name": "Inbox", "kind": "TASK"})
        tasks: dict[str, dict[str, str]] = {}
        seen: dict[str, dict[str, Any]] = {}
        count = 0
        open_count = 0
        for project_id, project in projects.items():
            if project.get("closed") is not None and type(project["closed"]) is not bool:
                raise TickTickError("TickTick-Listenstatus ist nicht verlässlich.")
            if project.get("closed") is True or project.get("kind") == "NOTE":
                continue
            if project.get("kind") not in (None, "TASK"):
                raise TickTickError("TickTick hat einen unbekannten Listentyp geliefert.")
            path_id = urllib.parse.quote(project_id, safe="")
            data = self._get(f"/project/{path_id}/data")
            if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
                raise TickTickError("TickTick-Listeninhalt ist unvollständig.")
            # This endpoint has no documented pagination. Reject unexpected truncation.
            if any(data.get(k) for k in ("hasMore", "nextPageToken", "nextCursor")):
                raise TickTickError("TickTick-Aufgabenabdeckung ist unvollständig.")
            returned = data.get("project")
            if returned is None and project_id == "inbox":
                # A null project and an empty array also fit an unsupported ID.
                # MCP virtual metadata or another unpaginated GET cannot prove access.
                raise TickTickError("TickTick-Inbox-Abdeckung über Open API ist nicht bestätigt; "
                                    "Antwortstruktur: " + json.dumps(project_data_shape(data), sort_keys=True) + ".")
            if not isinstance(returned, dict):
                area = "Inbox" if project_id == "inbox" else "Liste"
                shape = "fehlt/null" if returned is None else "hat einen ungültigen Typ"
                raise TickTickError(f"TickTick-{area}: project {shape}; Aufgabenarray vorhanden.")
            actual_id = identifier(returned.get("id"))
            if project_id != "inbox" and actual_id != project_id:
                raise TickTickError("TickTick-Listenidentität stimmt nicht überein.")
            if project_id == "inbox" and actual_id != "inbox" and actual_id in projects:
                raise TickTickError("TickTick-Inbox-Identität verweist auf eine andere bekannte Liste.")
            list_name = project.get("name") or returned.get("name") or (
                "Inbox" if project_id == "inbox" else "Unbenannte Liste")
            if not isinstance(list_name, str):
                raise TickTickError("TickTick hat einen ungültigen Listennamen geliefert.")
            count += 1
            for raw in data["tasks"]:
                if not isinstance(raw, dict):
                    raise TickTickError("TickTick hat eine ungültige Aufgabe geliefert.")
                task_id = identifier(raw.get("id"))
                if raw.get("projectId") not in (project_id, actual_id):
                    raise TickTickError("TickTick-Aufgabe gehört zu einer unerwarteten Liste.")
                if task_id in seen:
                    if seen[task_id] != raw:
                        raise TickTickError("TickTick-Aufgaben haben sich während des Abrufs geändert.")
                    continue
                seen[task_id] = raw
                task = normalize_task(raw, list_name)
                if task:
                    open_count += 1
                    if relevant(task, now):
                        tasks[task_id] = task
            self._remaining()
        self._remaining()
        return TaskRead(tasks=sorted(tasks.values(), key=lambda t: (
            t["due_date"] or t["start_date"] or "9999-12-31", t["due_at"] or t["start_at"], t["id"])),
            fetched_at=dt.datetime.now(BERLIN).isoformat(), project_count=count, open_task_count=open_count)


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


def read_tasks(access_token: str, now: dt.datetime, *, timeout_seconds: float = 15,
               total_timeout_seconds: float = 90) -> TaskRead:
    try:
        return TickTickClient(access_token, timeout_seconds=timeout_seconds,
                             total_timeout_seconds=total_timeout_seconds).read(now)
    except TickTickError as exc:
        # Discard all partial results. Never read an Apple snapshot as fallback.
        return TaskRead(warning=f"TickTick-Aufgaben nicht aktuell verfügbar: {exc} "
                        "Aufgabenstand unbekannt; keine gespeicherten Aufgaben verwendet.")


def format_task(task: dict[str, str]) -> str:
    when = (f"fällig {task['due']}" if task.get("due") else
            f"geplant {task['start']}" if task.get("start") else "ohne Termin")
    # The existing renderer HTML-escapes Markdown; neutralise Markdown in user text.
    def text(value: str) -> str:
        return re.sub(r"[\[\]*`\\]", "", " ".join(value.split()))
    notes = f" — {text(task['notes'])}" if task.get("notes") else ""
    return (f"- {text(task.get('title') or task.get('subject') or '')} ({text(task.get('list', ''))})"
            f" — {when}{notes} [TickTick]({task['source_url']})")
