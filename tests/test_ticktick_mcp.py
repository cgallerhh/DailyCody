import copy
import datetime as dt
import io
import json
from pathlib import Path
import sys
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ticktick_mcp as m
import ticktick_mcp_auth as auth
import ticktick_tasks as tt

TOKEN = "synthetic-private-mcp-access"
NOW = dt.datetime(2026, 10, 6, 6, tzinfo=tt.BERLIN)


def catalog():
    return [{"name": "list_projects", "annotations": {"readOnlyHint": True},
             "inputSchema": {"type": "object", "properties": {"offset": {"type": ["integer", "null"]}, "limit": {"type": ["integer", "null"]}}, "additionalProperties": False},
             "outputSchema": {"type": "object", "required": ["result"], "properties": {"result": {"type": "array", "items": {"type": "object"}}}}},
            {"name": "get_project_with_undone_tasks", "annotations": {"readOnlyHint": True},
             "inputSchema": {"type": "object", "required": ["project_id"], "properties": {"project_id": {"type": "string"}}, "additionalProperties": False},
             "outputSchema": {"type": "object", "properties": {"tasks": {"type": "array"}, "project": {"type": ["object", "null"]}}}}]


def task(key="probe", project="actual-inbox", **fields):
    return {"id": key, "projectId": project, "status": 0, "title": "SYNTHETIC PRIVATE TASK", **fields}


def response(data, *, headers=None, status=200, raw=None):
    r = io.BytesIO(json.dumps(data).encode() if raw is None else raw)
    r.headers = {"Content-Type": "application/json", **(headers or {})}
    r.status = status
    return r


def reader(responses):
    r = m.TaskReader(TOKEN)
    r.transport = Mock()
    r.transport.call = Mock(side_effect=responses)
    return r


class MCPTransportTest(unittest.TestCase):
    def transport(self):
        t = m.Transport(TOKEN)
        t.opener = Mock()
        return t

    def test_fixed_https_json_rpc_post_headers_correlation_and_no_redirect(self):
        t = self.transport()
        t.opener.open.return_value = response({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        self.assertEqual(t.rpc("tools/list", {}), {"ok": True})
        request = t.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://mcp.ticktick.com/")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Accept"), "application/json, text/event-stream")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertEqual(json.loads(request.data)["method"], "tools/list")
        self.assertLessEqual(t.opener.open.call_args.kwargs["timeout"], 15)
        with patch.object(m.urllib.request, "build_opener") as opener:
            m.Transport(TOKEN)
        self.assertIsInstance(opener.call_args.args[0], tt.NoRedirect)

    def test_write_tools_sampling_and_unknown_protocol_methods_are_blocked_before_http(self):
        for method, params in [("tools/call", {"name": "create_task"}), ("tools/call", {"name": "complete_task"}),
                               ("sampling/createMessage", {}), ("resources/read", {})]:
            t = self.transport()
            with self.subTest(method=method), self.assertRaises(m.MCPError):
                t.rpc(method, params)
            t.opener.open.assert_not_called()

    def test_rpc_error_wrong_id_bool_id_batch_and_invalid_json_are_redacted(self):
        for data in [{"jsonrpc": "2.0", "id": 1, "error": {"message": TOKEN}},
                     {"jsonrpc": "2.0", "id": 2, "result": TOKEN}, {"jsonrpc": "2.0", "id": True, "result": {}}, [],
                     {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}}]:
            t = self.transport()
            t.opener.open.return_value = response(data)
            with self.subTest(data=data), self.assertRaises(m.MCPError) as error:
                t.rpc("tools/list", {})
            self.assertNotIn(TOKEN, str(error.exception))
        t = self.transport()
        t.opener.open.return_value = response(None, raw=b"bad " + TOKEN.encode())
        with self.assertRaises(m.MCPError):
            t.rpc("tools/list", {})

    def test_streamable_http_sse_response_and_bounded_notifications(self):
        t = self.transport()
        raw = b': heartbeat\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
        raw += b'data: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
        t.opener.open.return_value = response(None, raw=raw, headers={"Content-Type": "text/event-stream"})
        self.assertEqual(t.rpc("tools/list", {}), {"ok": True})
        for raw in [b'data: {"jsonrpc":"2.0","id":99,"result":{}}\n\n',
                    b'data: {"jsonrpc":"2.0","method":"sampling/createMessage","id":1}\n\n', b'data: {}\n\n']:
            t = self.transport()
            t.opener.open.return_value = response(None, raw=raw, headers={"Content-Type": "text/event-stream"})
            with self.subTest(raw=raw), self.assertRaises(m.MCPError):
                t.rpc("tools/list", {})

    def test_session_version_notification_and_tool_catalog_pagination(self):
        t = self.transport()
        t.opener.open.side_effect = [
            response({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}}, headers={"Mcp-Session-Id": "synthetic-session"}),
            response(None, status=202, raw=b""),
            response({"jsonrpc": "2.0", "id": 3, "result": {"tools": catalog()[:1], "nextCursor": "page2"}}),
            response({"jsonrpc": "2.0", "id": 4, "result": {"tools": catalog()[1:]}})]
        t.start()
        self.assertEqual(set(t.schemas), m.TOOLS)
        for call in t.opener.open.call_args_list[1:]:
            req = call.args[0]
            self.assertEqual(req.get_header("Mcp-session-id"), "synthetic-session")
            self.assertEqual(req.get_header("Mcp-protocol-version"), "2025-06-18")
        self.assertNotIn("id", json.loads(t.opener.open.call_args_list[1].args[0].data))

    def test_catalog_missing_tool_wrong_required_argument_false_read_hint_and_cursor_loop_fail(self):
        variants = [catalog()[:1], [*catalog(), catalog()[0]], copy.deepcopy(catalog()), copy.deepcopy(catalog())]
        variants[2][1]["inputSchema"]["required"].append("date_filter")
        variants[3][0]["annotations"]["readOnlyHint"] = False
        for tools in variants:
            t = self.transport()
            t.rpc = Mock(side_effect=[{"protocolVersion": m.VERSIONS[0], "capabilities": {"tools": {}}}, None, {"tools": tools}])
            with self.subTest(tools=tools), self.assertRaises(m.MCPError):
                t.start()
        t = self.transport()
        t.rpc = Mock(side_effect=[{"protocolVersion": m.VERSIONS[0], "capabilities": {"tools": {}}}, None,
                                 {"tools": [], "nextCursor": "again"}, {"tools": [], "nextCursor": "again"}])
        with self.assertRaises(m.MCPError):
            t.start()

    def test_call_validates_input_output_and_error_content_without_leaking_values(self):
        t = self.transport()
        t.schemas = {v["name"]: v for v in catalog()}
        t.rpc = Mock(return_value={"structuredContent": {"result": []}})
        self.assertEqual(t.call("list_projects", {}), {"result": []})
        for name, args in [("delete_task", {}), ("list_projects", {"startDate": TOKEN}),
                           ("get_project_with_undone_tasks", {}), ("list_projects", {"offset": "wrong"})]:
            with self.subTest(name=name), self.assertRaises(m.MCPError):
                t.call(name, args)
        for result in [{"isError": True, "content": [{"type": "text", "text": TOKEN}]},
                       {"structuredContent": {"result": None}}, {"content": [{"type": "text", "text": TOKEN}]}]:
            t.rpc.return_value = result
            with self.subTest(result=result), self.assertRaises(m.MCPError) as error:
                t.call("list_projects", {})
            self.assertNotIn(TOKEN, str(error.exception))
        t.rpc.return_value = {"content": [{"type": "text", "text": '{"result":[]}'}]}
        self.assertEqual(t.call("list_projects", {}), {"result": []})

    def test_schema_refs_nullable_fields_required_types_and_unsupported_assertions(self):
        schema = {"type": "object", "$defs": {"id": {"type": "string"}}, "properties": {"id": {"$ref": "#/$defs/id"}, "project": {"anyOf": [{"type": "object"}, {"type": "null"}]}}, "required": ["id"]}
        m.validate({"id": "i", "project": None}, schema)
        for value, rule in [({"id": 3}, schema), ({}, schema), ("x", {"pattern": ".*"}),
                            ("x", {"$ref": "https://evil.invalid/"}), (True, {"type": "integer"})]:
            with self.subTest(value=value), self.assertRaises(m.MCPError):
                m.validate(value, rule)

    def test_http_auth_redirect_session_expiry_retries_deadline_and_size(self):
        for code in (401, 403, 302):
            t = self.transport()
            t.opener.open.side_effect = urllib.error.HTTPError("https://private/" + TOKEN, code, TOKEN, {}, io.BytesIO(TOKEN.encode()))
            with self.subTest(code=code), self.assertRaises(m.MCPError) as error:
                t.rpc("tools/list", {})
            self.assertNotIn(TOKEN, str(error.exception))
            self.assertEqual(t.opener.open.call_count, 1)
        t = self.transport()
        t._session = "synthetic-session"
        t.opener.open.side_effect = urllib.error.HTTPError(m.URL, 404, "", {}, io.BytesIO())
        with self.assertRaises(m.SessionExpired):
            t.rpc("tools/list", {})
        for code in (429, 503):
            t = self.transport()
            t.opener.open.side_effect = [urllib.error.HTTPError(m.URL, code, TOKEN, {}, io.BytesIO()), response({"jsonrpc": "2.0", "id": 1, "result": {}})]
            with patch.object(m.time, "sleep") as sleep:
                self.assertEqual(t.rpc("tools/list", {}), {})
            sleep.assert_called_once_with(1)
        t = self.transport()
        with patch.object(m.time, "monotonic", return_value=t.deadline), self.assertRaises(m.MCPError):
            t.rpc("tools/list", {})
        t.opener.open.assert_not_called()
        t = self.transport()
        t.opener.open.return_value = response(None, raw=b"x" * (tt.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(m.MCPError):
            t.rpc("tools/list", {})

    def test_exhausted_network_retries_and_late_response_discard_all_data(self):
        t = self.transport()
        t.opener.open.side_effect = urllib.error.URLError(TOKEN)
        with patch.object(m.time, "sleep"), self.assertRaises(m.MCPError) as error:
            t.rpc("tools/list", {})
        self.assertEqual(t.opener.open.call_count, 3)
        self.assertNotIn(TOKEN, str(error.exception))
        t = self.transport()
        t.opener.open.return_value = response({"jsonrpc": "2.0", "id": 1, "result": {}})
        with patch.object(m.time, "monotonic", side_effect=[t.deadline - 1, t.deadline + 1]), self.assertRaises(m.MCPError):
            t.rpc("tools/list", {})


class MCPTaskCoverageTest(unittest.TestCase):
    def test_all_lists_inbox_230_tasks_status_dedup_and_undated_id_probe(self):
        project = {"id": "p", "name": "Privat"}
        inbox = {"id": "inbox", "name": "Inbox"}
        records = [task(str(i), "p", dueDate="2026-10-06T08:00:00+0000") for i in range(230)]
        records += [copy.deepcopy(records[0]), task("completed", "p", status=2)]
        r = reader([{"result": [project, inbox]}, {"result": [project]},
                    {"project": project, "tasks": records}, {"project": None, "tasks": [task()]}])
        result = r.read(NOW, required_inbox_task_id="probe")
        self.assertEqual(result.open_task_count, 231)
        self.assertEqual(result.project_count, 2)
        self.assertTrue(result.inbox_probe_verified)
        self.assertEqual(next(t for t in result.tasks if t["id"] == "probe")["due_date"], "")
        for call in r.transport.call.call_args_list:
            self.assertTrue(set(call.args[1]).issubset({"offset", "limit", "project_id"}))
        self.assertIn(("get_project_with_undone_tasks", {"project_id": "inbox"}), [c.args for c in r.transport.call.call_args_list])

    def test_project_pagination_over_200_lists_skips_closed_and_notes(self):
        projects = [{"id": "p" + str(i)} for i in range(201)]
        r = reader([{"result": [projects[0], {"id": "inbox"}]},
                    {"result": projects[:100]}, {"result": projects[100:200]}, {"result": projects[200:]},
                    {"project": projects[0], "tasks": []}, {"project": None, "tasks": []},
                    *[{"project": p, "tasks": []} for p in projects[1:]]])
        self.assertEqual(r.read(NOW).project_count, 202)
        self.assertEqual(r.transport.call.call_args_list[3].args[1]["offset"], 200)

    def test_empty_inbox_is_never_sufficient_for_required_probe(self):
        for rows in [[], [task(status=2)], [task(dueDate="2026-10-06")], [task(startDate="2026-10-06")], [task("another")]]:
            r = reader([{"result": [{"id": "inbox"}]}, {"result": []}, {"project": None, "tasks": rows}])
            with self.subTest(rows=rows), self.assertRaises(m.MCPError):
                r.read(NOW, required_inbox_task_id="probe")

    def test_missing_inbox_missing_tasks_continuation_mixed_ids_and_repeated_pages_fail(self):
        variants = [[{"result": []}], [{"result": [{"id": "inbox"}], "hasMore": True}],
                    [{"result": [{"id": "inbox"}]}, {"result": []}, {"project": None}],
                    [{"result": [{"id": "inbox"}]}, {"result": []}, {"tasks": [], "nextCursor": "cursor"}],
                    [{"result": [{"id": "inbox"}]}, {"result": []}, {"tasks": [task(), task("x", "other-inbox")]}],
                    [{"result": [{"id": "inbox"}]}, {"result": [{"id": str(i)} for i in range(100)]}, {"result": [{"id": str(i)} for i in range(100)]}]]
        for responses in variants:
            with self.subTest(responses=responses), self.assertRaises(m.MCPError):
                reader(responses).read(NOW)

    def test_session_restart_discards_partial_data_and_reloads_schema(self):
        p = {"id": "p"}
        start = [{"result": [p, {"id": "inbox"}]}, {"result": [p]}]
        r = reader([*start, {"project": p, "tasks": [task("old", "p")]}, m.SessionExpired("expired"),
                    *start, {"project": p, "tasks": [task("new", "p")]}, {"project": None, "tasks": [task()]}])
        result = r.read(NOW, required_inbox_task_id="probe")
        self.assertEqual({t["id"] for t in result.tasks}, {"new", "probe"})
        self.assertEqual(r.transport.start.call_count, 2)

    def test_every_read_is_fresh_and_partial_failure_cannot_use_open_api_or_apple(self):
        state = auth.State("synthetic-client", TOKEN, expires_at=time.time() + 3600).to_json()
        with patch.object(m, "TaskReader") as factory:
            factory.return_value.read.side_effect = m.MCPError("synthetic failure")
            result = tt.read_tasks(state, NOW)
        self.assertEqual(result.tasks, [])
        self.assertFalse(result.status()["available"])
        self.assertFalse(hasattr(tt, "TickTickClient"))
        self.assertFalse(hasattr(tt, "read_open_api_reference"))
        with patch.object(m, "TaskReader") as factory:
            factory.return_value.read.return_value = tt.TaskRead()
            tt.read_tasks(state, NOW)
            tt.read_tasks(state, NOW)
        self.assertEqual(factory.call_count, 2)

    def test_shared_budget_failure_is_not_a_successful_partial_result(self):
        r = reader([{"result": [{"id": "inbox"}]}, {"result": []}, {"project": None, "tasks": [task()]}])
        r.transport.remaining.side_effect = m.MCPError("deadline")
        with self.assertRaises(m.MCPError):
            r.read(NOW)
