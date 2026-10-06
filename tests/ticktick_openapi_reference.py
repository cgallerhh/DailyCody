"""Historical Open API failure regression fixture, never imported by production."""
import datetime as dt
import http.client
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from ticktick_tasks import (BERLIN, MAX_RESPONSE_BYTES, NoRedirect, TaskRead, TickTickError,
                           identifier, normalize_task, relevant, project_data_shape)
API = "https://api.ticktick.com/open/v1"

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


def read_open_api_reference(access_token: str, now: dt.datetime, *, timeout_seconds: float = 15,
                            total_timeout_seconds: float = 90) -> TaskRead:
    """Historical failed Open API contract only; never called by Cody or MCP."""
    try:
        return TickTickClient(access_token, timeout_seconds=timeout_seconds,
                             total_timeout_seconds=total_timeout_seconds).read(now)
    except TickTickError as exc:
        # Discard all partial results. Never read an Apple snapshot as fallback.
        return TaskRead(warning=f"TickTick-Aufgaben nicht aktuell verfügbar: {exc} "
                        "Aufgabenstand unbekannt; keine gespeicherten Aufgaben verwendet.")

