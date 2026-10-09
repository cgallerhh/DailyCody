"""Small read-only Streamable HTTP client for TickTick's official remote MCP.

No task mutation, sampling, browser cookies, redirects or connector-token lookup.
Remote tool schemas are validated at every new session, before any tool call.
"""
from __future__ import annotations

import datetime as dt
import http.client
import json
import math
import time
import urllib.error
import urllib.request
from typing import Any

import ticktick_tasks as tt

URL = "https://mcp.ticktick.com/"
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
TOOLS = frozenset(("list_projects", "get_project_with_undone_tasks"))


class MCPError(RuntimeError):
    """Only redacted diagnostics; never server error messages or response bodies."""


class SessionExpired(MCPError):
    pass


def validate(value: Any, schema: Any, root: Any = None, depth: int = 0) -> None:
    """Validate the structural JSON Schema subset; refuse unsupported assertions."""
    if depth > 50 or not isinstance(schema, (dict, bool)):
        raise MCPError("TickTick-MCP-Schema ist nicht verlässlich.")
    if schema is True:
        return
    if schema is False:
        raise MCPError("TickTick-MCP-Daten verletzen das Schema.")
    root = schema if root is None else root
    supported = {"$schema", "$id", "$defs", "definitions", "$ref", "title", "description", "default",
                 "examples", "deprecated", "readOnly", "writeOnly", "type", "enum", "const", "anyOf", "oneOf",
                 "allOf", "required", "properties", "additionalProperties", "items", "minItems", "maxItems",
                 "minLength", "maxLength", "minimum", "maximum", "format"}
    if set(schema) - supported:
        raise MCPError("TickTick-MCP-Schema benötigt eine nicht unterstützte Prüfung.")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise MCPError("TickTick-MCP-Schema enthält einen unzulässigen Verweis.")
        target = root
        try:
            for key in ref[2:].split("/"):
                target = target[key.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise MCPError("TickTick-MCP-Schemaverweis fehlt.") from None
        validate(value, target, root, depth + 1)
    for keyword in ("anyOf", "oneOf"):
        if keyword in schema:
            choices = schema[keyword]
            if not isinstance(choices, list) or not choices:
                raise MCPError("TickTick-MCP-Schema ist nicht verlässlich.")
            matches = 0
            for choice in choices:
                try:
                    validate(value, choice, root, depth + 1)
                    matches += 1
                except MCPError:
                    pass
            if matches == 0 or (keyword == "oneOf" and matches != 1):
                raise MCPError("TickTick-MCP-Daten verletzen das Schema.")
    if "allOf" in schema:
        for choice in schema["allOf"]:
            validate(value, choice, root, depth + 1)
    types = {"null": value is None, "object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value), "boolean": type(value) is bool}
    if "type" in schema:
        expected = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not expected or any(t not in types for t in expected) or not any(types[t] for t in expected):
            raise MCPError("TickTick-MCP-Datentyp stimmt nicht.")
    if ("enum" in schema and value not in schema["enum"]) or ("const" in schema and value != schema["const"]):
        raise MCPError("TickTick-MCP-Wert verletzt das Schema.")
    if isinstance(value, dict):
        if any(k not in value for k in schema.get("required", [])):
            raise MCPError("TickTick-MCP-Pflichtfeld fehlt.")
        props = schema.get("properties", {})
        for key, child in value.items():
            rule = props.get(key, schema.get("additionalProperties", True))
            validate(child, rule, root, depth + 1)
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            raise MCPError("TickTick-MCP-Array verletzt das Schema.")
        for child in value:
            validate(child, schema.get("items", True), root, depth + 1)
    if isinstance(value, str) and (len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf)):
        raise MCPError("TickTick-MCP-Textlänge verletzt das Schema.")
    if type(value) in (int, float) and (value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf)):
        raise MCPError("TickTick-MCP-Zahl verletzt das Schema.")


class Transport:
    def __init__(self, token: str, *, timeout: float = 15, deadline: float | None = None):
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise MCPError("TickTick-MCP-Zugriff ist nicht eingerichtet.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise MCPError("TickTick-MCP-Zeitlimit ist ungültig.")
        self._token = token
        self.timeout = timeout
        self.deadline = deadline if deadline is not None else time.monotonic() + 90
        self.opener = urllib.request.build_opener(tt.NoRedirect())
        self._session = None
        self.version = None
        self.serial = 0
        self.schemas: dict[str, dict] = {}

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise MCPError("TickTick-MCP-Abruf hat sein Zeitlimit überschritten.")
        return remaining

    def _decode(self, response, expected: int) -> dict:
        kind = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if kind == "application/json":
            payload = response.read(tt.MAX_RESPONSE_BYTES + 1)
            if len(payload) > tt.MAX_RESPONSE_BYTES:
                raise MCPError("TickTick-MCP-Antwort überschreitet das Limit.")
            self.remaining()
            result = json.loads(payload)
        elif kind == "text/event-stream":
            total, fields, result = 0, [], None
            for _ in range(10000):
                line = response.readline(65537)
                self.remaining()
                total += len(line)
                if len(line) > 65536 or total > tt.MAX_RESPONSE_BYTES:
                    raise MCPError("TickTick-MCP-Stream überschreitet das Limit.")
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                if line.startswith("data:"):
                    fields.append(line[5:].lstrip(" "))
                if not line and fields:
                    message = json.loads("\n".join(fields))
                    fields = []
                    if not isinstance(message, dict):
                        raise MCPError("TickTick-MCP-Stream ist ungültig.")
                    if "id" in message:
                        result = message
                        break
                    if message.get("jsonrpc") != "2.0" or not str(message.get("method", "")).startswith("notifications/"):
                        raise MCPError("TickTick-MCP fordert eine nicht erlaubte Client-Aktion.")
            if result is None:
                raise MCPError("TickTick-MCP-Stream enthält keine vollständige Antwort.")
        else:
            raise MCPError("TickTick-MCP-Antworttyp ist nicht unterstützt.")
        if (not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or
                type(result.get("id")) is not int or result["id"] != expected or
                ("result" in result) == ("error" in result)):
            raise MCPError("TickTick-MCP-Antwortidentität ist ungültig.")
        if "error" in result:
            raise MCPError("TickTick-MCP hat den lesenden Aufruf abgelehnt.")
        return result["result"]

    def rpc(self, method: str, params: dict, *, notification: bool = False):
        if method not in ("initialize", "notifications/initialized", "tools/list", "tools/call"):
            raise MCPError("TickTick-MCP-Methode ist nicht erlaubt.")
        if method == "tools/call" and params.get("name") not in TOOLS:
            raise MCPError("TickTick-MCP-Tool ist nicht lesend freigegeben.")
        if notification != (method == "notifications/initialized"):
            raise MCPError("TickTick-MCP-Nachrichtentyp ist ungültig.")
        self.serial += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            body["id"] = self.serial
        headers = {"Authorization": "Bearer " + self._token, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "Cache-Control": "no-cache"}
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers=headers)
        for attempt in range(3):
            try:
                with self.opener.open(req, timeout=min(self.timeout, self.remaining())) as response:
                    if notification:
                        if response.status != 202 or response.read(1):
                            raise MCPError("TickTick-MCP-Initialisierung wurde nicht bestätigt.")
                        return None
                    result = self._decode(response, self.serial)
                    if method == "initialize":
                        session = response.headers.get("Mcp-Session-Id")
                        if session is not None and (not session or len(session) > 4096 or any(not 33 <= ord(c) <= 126 for c in session)):
                            raise MCPError("TickTick-MCP-Session ist ungültig.")
                        self._session = session
                    return result
            except urllib.error.HTTPError as exc:
                code = exc.code
                exc.close()
                if code == 404 and self._session:
                    raise SessionExpired("TickTick-MCP-Session ist abgelaufen.") from None
                if code in (401, 403):
                    raise MCPError("TickTick-MCP-Autorisierung fehlt, ist abgelaufen oder unzureichend.") from None
                if code != 429 and not 500 <= code <= 599:
                    raise MCPError(f"TickTick-MCP-Aufruf fehlgeschlagen (HTTP {code}).") from None
            except (urllib.error.URLError, OSError, TimeoutError, http.client.HTTPException):
                pass
            except (ValueError, UnicodeError, RecursionError, TypeError):
                raise MCPError("TickTick-MCP lieferte ungültige Daten.") from None
            if attempt == 2:
                raise MCPError("TickTick-MCP ist nach begrenzten Wiederholungen nicht erreichbar.")
            delay = 2 ** attempt
            if self.remaining() <= delay:
                raise MCPError("TickTick-MCP-Abruf hat sein Zeitlimit überschritten.")
            time.sleep(delay)

    def start(self) -> None:
        self._session, self.version, self.schemas = None, None, {}
        result = self.rpc("initialize", {"protocolVersion": VERSIONS[0], "capabilities": {},
                                         "clientInfo": {"name": "daily-cody-read-only", "version": "1"}})
        if not isinstance(result, dict) or result.get("protocolVersion") not in VERSIONS or not isinstance(result.get("capabilities", {}).get("tools"), dict):
            raise MCPError("TickTick-MCP-Protokoll oder Tool-Fähigkeit ist nicht unterstützt.")
        self.version = result["protocolVersion"]
        self.rpc("notifications/initialized", {}, notification=True)
        cursor, seen = None, set()
        for _ in range(100):
            page = self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(page, dict) or not isinstance(page.get("tools"), list):
                raise MCPError("TickTick-MCP-Toolkatalog ist unvollständig.")
            for tool in page["tools"]:
                if not isinstance(tool, dict):
                    raise MCPError("TickTick-MCP-Toolkatalog ist ungültig.")
                name = tool.get("name")
                if not isinstance(name, str):
                    raise MCPError("TickTick-MCP-Toolname ist ungültig.")
                if name not in TOOLS:
                    continue
                if name in self.schemas:
                    raise MCPError("TickTick-MCP-Toolkatalog enthält doppelte Lesetools.")
                schema = tool.get("inputSchema")
                annotations = tool.get("annotations") or {}
                expected = {"offset", "limit"} if name == "list_projects" else {"project_id"}
                if (not isinstance(schema, dict) or schema.get("type") != "object" or
                        not expected.issubset(schema.get("properties", {})) or
                        set(schema.get("required", [])) - expected or
                        (name == "get_project_with_undone_tasks" and "project_id" not in schema.get("required", [])) or
                        not isinstance(annotations, dict) or annotations.get("readOnlyHint") is False):
                    raise MCPError("TickTick-MCP-Lesetool-Schema ist inkompatibel.")
                self.schemas[name] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen:
                raise MCPError("TickTick-MCP-Toolpagination ist ungültig.")
            seen.add(cursor)
        else:
            raise MCPError("TickTick-MCP-Toolpagination überschreitet das Limit.")
        if set(self.schemas) != TOOLS:
            raise MCPError("TickTick-MCP benötigt beide freigegebenen Lesetools.")

    def call(self, name: str, arguments: dict):
        allowed = {"offset", "limit"} if name == "list_projects" else {"project_id"}
        if name not in TOOLS or name not in self.schemas or set(arguments) - allowed:
            raise MCPError("TickTick-MCP-Tool oder Argument ist nicht erlaubt.")
        validate(arguments, self.schemas[name]["inputSchema"])
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict) or (result.get("isError") is not None and result.get("isError") is not False):
            raise MCPError("TickTick-MCP-Lesetool meldet einen Fehler.")
        if "structuredContent" in result:
            data = result["structuredContent"]
        else:
            content = result.get("content")
            if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or content[0].get("type") != "text":
                raise MCPError("TickTick-MCP-Leseantwort ist unvollständig.")
            try:
                data = json.loads(content[0]["text"])
            except (ValueError, KeyError, TypeError):
                raise MCPError("TickTick-MCP-Leseantwort ist nicht strukturiert.") from None
        schema = self.schemas[name].get("outputSchema")
        if schema is not None:
            validate(data, schema)
        return data


class TaskReader:
    def __init__(self, token: str, *, timeout_seconds: float = 15, total_timeout_seconds: float = 90):
        if not math.isfinite(total_timeout_seconds) or total_timeout_seconds <= 0:
            raise MCPError("TickTick-MCP-Gesamtzeitlimit ist ungültig.")
        self.transport = Transport(token, timeout=timeout_seconds, deadline=time.monotonic() + total_timeout_seconds)

    def read(self, now: dt.datetime, *, required_inbox_task_id: str | None = None) -> tt.TaskRead:
        for attempt in range(2):
            try:
                self.transport.start()
                return self._read(now, required_inbox_task_id)
            except SessionExpired:
                if attempt:
                    raise MCPError("TickTick-MCP-Session konnte nicht wiederhergestellt werden.") from None
        raise AssertionError("unreachable")

    def _read(self, now: dt.datetime, expected: str | None) -> tt.TaskRead:
        projects = {}
        def add(records):
            if (not isinstance(records, dict) or not isinstance(records.get("result"), list) or
                    any(records.get(k) for k in ("hasMore", "nextCursor", "nextPageToken"))):
                raise MCPError("TickTick-MCP-Listenformat ist unvollständig.")
            for project in records["result"]:
                if not isinstance(project, dict):
                    raise MCPError("TickTick-MCP-Liste ist ungültig.")
                key = tt.identifier(project.get("id"))
                if key in projects and projects[key] != project:
                    raise MCPError("TickTick-MCP-Listen änderten sich während des Abrufs.")
                projects[key] = project
            return records["result"]
        add(self.transport.call("list_projects", {}))
        if "inbox" not in projects:
            raise MCPError("TickTick-MCP-Inbox fehlt in der frischen Listenübersicht.")
        page_ids = set()
        for page in range(100):
            rows = add(self.transport.call("list_projects", {"offset": page * 100, "limit": 100}))
            ids = {p["id"] for p in rows}
            if len(rows) > 100 or (rows and ids.issubset(page_ids)):
                raise MCPError("TickTick-MCP-Listenpagination ist nicht vollständig.")
            page_ids.update(ids)
            if len(rows) < 100:
                break
        else:
            raise MCPError("TickTick-MCP-Listenpagination überschreitet das Limit.")
        if set(projects) - {"inbox"} - page_ids:
            raise MCPError("TickTick-MCP-Listenabdeckung widerspricht der Pagination.")
        tasks, seen, count, open_count, probe = {}, {}, 0, 0, False
        for key, project in projects.items():
            closed = project.get("closed")
            if closed is not None and type(closed) is not bool:
                raise MCPError("TickTick-MCP-Listenstatus ist ungültig.")
            if closed is True or project.get("kind") == "NOTE":
                if key == "inbox":
                    raise MCPError("TickTick-MCP-Inbox ist keine aktive Aufgabenliste.")
                continue
            if project.get("kind") not in (None, "TASK"):
                raise MCPError("TickTick-MCP-Listentyp ist unbekannt.")
            data = self.transport.call("get_project_with_undone_tasks", {"project_id": key})
            if (not isinstance(data, dict) or not isinstance(data.get("tasks"), list) or
                    any(data.get(k) for k in ("hasMore", "nextCursor", "nextPageToken"))):
                raise MCPError("TickTick-MCP-Aufgabenabdeckung ist unvollständig.")
            returned = data.get("project")
            if returned is None and key == "inbox":
                ids = {tt.identifier(t.get("projectId")) for t in data["tasks"] if isinstance(t, dict)}
                if len(ids) > 1 or any(i != "inbox" and i in projects for i in ids):
                    raise MCPError("TickTick-MCP-Inbox-Identität ist widersprüchlich.")
                actual = next(iter(ids), "inbox")
            elif isinstance(returned, dict):
                actual = tt.identifier(returned.get("id"))
                if key != "inbox" and actual != key:
                    raise MCPError("TickTick-MCP-Listenidentität stimmt nicht.")
                if key == "inbox" and actual != key and actual in projects:
                    raise MCPError("TickTick-MCP-Inbox verweist auf eine andere Liste.")
            else:
                raise MCPError("TickTick-MCP-Listenidentität fehlt.")
            name = project.get("name") or ("Inbox" if key == "inbox" else "Unbenannte Liste")
            if not isinstance(name, str):
                raise MCPError("TickTick-MCP-Listenname ist ungültig.")
            count += 1
            for raw in data["tasks"]:
                if not isinstance(raw, dict) or raw.get("projectId") not in (key, actual):
                    raise MCPError("TickTick-MCP-Aufgabenliste ist widersprüchlich.")
                task_id = tt.identifier(raw.get("id"))
                if task_id in seen:
                    if seen[task_id] != raw:
                        raise MCPError("TickTick-MCP-Aufgaben änderten sich während des Abrufs.")
                    continue
                seen[task_id] = raw
                task = tt.normalize_task(raw, name)
                if task:
                    open_count += 1
                    if key == "inbox" and task_id == expected and not task["due_date"] and not task["start_date"]:
                        probe = True
                    if tt.relevant(task, now):
                        tasks[task_id] = task
            self.transport.remaining()
        if expected is not None and not probe:
            raise MCPError("TickTick-MCP-Abnahme: bekannte offene undatierte Inbox-Aufgabe wurde nicht gefunden.")
        self.transport.remaining()
        return tt.TaskRead(tasks=sorted(tasks.values(), key=lambda t: (t["due_date"] or t["start_date"] or "9999-12-31", t["id"])),
                           fetched_at=dt.datetime.now(tt.BERLIN).isoformat(), project_count=count,
                           open_task_count=open_count, inbox_probe_verified=probe)
