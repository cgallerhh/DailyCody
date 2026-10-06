import copy
import datetime as dt
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.parse
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody
import ticktick_tasks as tt

NOW = dt.datetime(2026, 10, 6, 6, tzinfo=tt.BERLIN)
TOKEN = "synthetic-token-never-real"


def task(task_id="a", project="p", **values):
    return {"id": task_id, "projectId": project, "title": "Aufgabe " + task_id,
            "status": 0, "isAllDay": False, **values}


def project_data(project_id="p", tasks=None):
    return {"project": {"id": project_id}, "tasks": tasks or []}


def client_responses(responses, **kwargs):
    client = tt.TickTickClient(TOKEN, **kwargs)
    client._get = Mock(side_effect=responses)
    return client


class TickTickCoverageTest(unittest.TestCase):
    def test_all_pages_active_lists_and_inbox_are_read_without_task_limit(self):
        projects = [{"id": "p", "name": "Privat"}, {"id": "q", "name": "Arbeit"}]
        projects2 = [{"id": "notes", "kind": "NOTE"}, {"id": "closed", "closed": True}]
        many = [task(str(i), dueDate="2026-10-06T08:00:00+0000") for i in range(230)]
        client = client_responses([projects, projects2, [], project_data("p", many),
                                   project_data("q", [task("q1", "q")]),
                                   project_data("real-inbox", [task("i1", "real-inbox")])], page_size=2)
        result = client.read(NOW)
        self.assertEqual(len(result.tasks), 232)
        self.assertEqual(result.project_count, 3)
        calls = [c.args[0] for c in client._get.call_args_list]
        self.assertEqual(calls[:3], ["/project?offset=0&limit=2", "/project?offset=2&limit=2", "/project?offset=4&limit=2"])
        self.assertEqual(calls[3:], ["/project/p/data", "/project/q/data", "/project/inbox/data"])
        self.assertEqual(next(t for t in result.tasks if t["id"] == "i1")["list"], "Inbox")

    def test_empty_account_still_reads_inbox(self):
        client = client_responses([[], project_data("inbox")])
        result = client.read(NOW)
        self.assertEqual(result.tasks, [])
        self.assertTrue(result.fetched_at)
        self.assertEqual(result.project_count, 1)

    def test_empty_virtual_inbox_allows_null_or_omitted_project_after_fresh_identity_check(self):
        for data in [{"project": None, "tasks": [], "columns": []}, {"tasks": []}]:
            with self.subTest(data=data):
                client = client_responses([[], data, [{"id": "inbox", "name": "Inbox", "kind": "TASK"}]])
                result = client.read(NOW)
                self.assertEqual(result.tasks, [])
                self.assertEqual(result.project_count, 1)
                self.assertTrue(result.status()["available"])
                self.assertEqual([c.args[0] for c in client._get.call_args_list],
                                 ["/project?offset=0&limit=100", "/project/inbox/data", "/project"])

    def test_virtual_inbox_without_project_preserves_all_tasks_ids_dates_and_status(self):
        records = [task(str(i), "inbox-account-id", isAllDay=True, dueDate="2026-10-06") for i in range(230)]
        records += [copy.deepcopy(records[0]), task("done", "inbox-account-id", status=2),
                    task("abandoned", "inbox-account-id", status=-1), task("undated", "inbox-account-id")]
        client = client_responses([[], {"project": None, "tasks": records}, [{"id": "inbox"}]])
        result = client.read(NOW)
        self.assertEqual(len(result.tasks), 231)
        self.assertEqual(result.open_task_count, 231)
        self.assertTrue(all(t["list"] == "Inbox" for t in result.tasks))
        self.assertTrue(all(t["project_id"] == "inbox-account-id" for t in result.tasks))
        self.assertIn("#p/inbox-account-id/tasks/0", next(t for t in result.tasks if t["id"] == "0")["source_url"])
        self.assertEqual(next(t for t in result.tasks if t["id"] == "0")["due_date"], "2026-10-06")
        self.assertEqual(next(t for t in result.tasks if t["id"] == "undated")["due_date"], "")

    def test_virtual_inbox_identity_missing_or_malformed_never_counts_as_empty(self):
        for listing in [[], {}, [None], [{"id": "inbox"}, {"id": "inbox"}],
                        [{"id": "inbox", "closed": True}], [{"id": "inbox", "closed": "false"}],
                        [{"id": "inbox", "closed": 0}],
                        [{"id": "inbox", "kind": "NOTE"}], [{"id": "../unsafe"}]]:
            with self.subTest(listing=listing):
                client = client_responses([[], {"project": None, "tasks": []}, listing])
                with patch.object(tt, "TickTickClient", return_value=client):
                    result = tt.read_tasks(TOKEN, NOW)
                self.assertEqual(result.tasks, [])
                self.assertFalse(result.status()["available"])
                self.assertIn("Aufgabenstand unbekannt", result.warning)

    def test_unknown_inbox_discards_previously_read_tasks(self):
        client = client_responses([[{"id": "p"}], project_data("p", [task()]),
                                   {"project": None, "tasks": []}, [{"id": "p"}]])
        with patch.object(tt, "TickTickClient", return_value=client):
            result = tt.read_tasks(TOKEN, NOW)
        self.assertEqual(result.tasks, [])
        self.assertIn("TickTick-Inbox", result.warning)
        self.assertEqual(result.project_count, 0)

    def test_normal_list_null_project_still_fails_with_safe_stage_diagnostic(self):
        client = client_responses([[{"id": "p", "name": TOKEN}], {"project": None, "tasks": []}])
        with self.assertRaises(tt.TickTickError) as error:
            client.read(NOW)
        self.assertIn("TickTick-Liste: project fehlt/null", str(error.exception))
        self.assertNotIn(TOKEN, str(error.exception))
        self.assertEqual(client._get.call_count, 2)

    def test_inbox_rejects_mixed_known_or_invalid_task_project_ids(self):
        for records in [[task("a", "inbox"), task("b", "other-inbox")], [task("a", "p")],
                        [task("a", "../invalid")], [None], [task("a", None)]]:
            with self.subTest(records=records), self.assertRaises(tt.TickTickError):
                client_responses([[{"id": "p"}], project_data("p"),
                                  {"project": None, "tasks": records}, [{"id": "inbox"}, {"id": "p"}]]).read(NOW)

    def test_inbox_missing_tasks_invalid_project_or_continuation_still_fails(self):
        for data in [{"project": None}, {"project": None, "tasks": None},
                     {"project": "invalid", "tasks": []}, {"project": {}, "tasks": []},
                     {"project": None, "tasks": [], "nextCursor": "private-cursor"}]:
            with self.subTest(data=data), self.assertRaises(tt.TickTickError):
                client = client_responses([[], data])
                client.read(NOW)
            self.assertEqual(client._get.call_count, 2)

    def test_virtual_inbox_identity_check_uses_shared_timeout_and_discards_late_result(self):
        client = client_responses([[], {"project": None, "tasks": []}, [{"id": "inbox"}]])
        with patch.object(tt.time, "monotonic", side_effect=[0, 91]), self.assertRaises(tt.TickTickError):
            client.read(NOW)

    def test_virtual_inbox_is_not_read_twice(self):
        client = client_responses([[{"id": "inbox", "name": "Inbox"}], project_data("inbox", [task("i", "inbox")])])
        self.assertEqual(len(client.read(NOW).tasks), 1)
        self.assertEqual(client._get.call_count, 2)

    def test_duplicate_ids_are_removed_but_same_titles_are_preserved(self):
        a, b = task(), task("b", title="Aufgabe a")
        client = client_responses([[{"id": "p"}], project_data("p", [a, copy.deepcopy(a), b]), project_data("inbox")])
        self.assertEqual([t["id"] for t in client.read(NOW).tasks], ["a", "b"])

    def test_completed_abandoned_and_notes_are_not_briefing_tasks(self):
        records = [task("done", status=2), task("abandoned", status=-1),
                   task("note", kind="NOTE"), task("recurring", repeatFlag="RRULE:FREQ=DAILY", completedTime="2026-10-05T12:00:00+0000")]
        client = client_responses([[{"id": "p"}], project_data("p", records), project_data("inbox")])
        self.assertEqual([t["id"] for t in client.read(NOW).tasks], ["recurring"])

    def test_repeated_pagination_and_page_limit_fail_explicitly(self):
        for responses, max_pages in [([[{"id": "p"}], [{"id": "p"}]], 5), ([[{"id": "p"}]], 1)]:
            with self.subTest(max_pages=max_pages):
                client = client_responses(responses, page_size=1, max_pages=max_pages)
                with self.assertRaises(tt.TickTickError):
                    client.read(NOW)

    def test_partial_failure_never_returns_partial_or_apple_tasks(self):
        client = client_responses([[{"id": "p"}], project_data("p", [task()]), tt.TickTickError("synthetischer Fehler")])
        with patch.object(tt, "TickTickClient", return_value=client), patch.object(daily_cody, "read_exported_reminders") as apple:
            result = tt.read_tasks(TOKEN, NOW)
        self.assertEqual(result.tasks, [])
        self.assertFalse(result.status()["available"])
        self.assertIn("Aufgabenstand unbekannt", result.warning)
        apple.assert_not_called()

    def test_malformed_and_truncated_project_data_fail(self):
        for data in [{}, {"tasks": []}, project_data("other", [task()]),
                     {**project_data(), "hasMore": True}, project_data("p", [task(project="other")]),
                     project_data("p", [task("same"), task("same", status=2)]),
                     project_data("p", [{"id": "bad", "projectId": "p", "title": "Unknown status"}])]:
            with self.subTest(data=data):
                with self.assertRaises(tt.TickTickError):
                    client_responses([[{"id": "p"}], data]).read(NOW)

    def test_every_briefing_call_fetches_again(self):
        client = client_responses([[], project_data("inbox", [task("old", "inbox")]),
                                   [], project_data("inbox", [task("new", "inbox")])])
        self.assertEqual(client.read(NOW).tasks[0]["id"], "old")
        self.assertEqual(client.read(NOW).tasks[0]["id"], "new")

    def test_malformed_project_pages_and_unsafe_ids_are_rejected(self):
        for page in [{"result": []}, [None], [{"id": "../wrong"}], [{"id": "p"}, {"id": "q"}]]:
            with self.subTest(page=page), self.assertRaises(tt.TickTickError):
                client_responses([page], page_size=1).read(NOW)

    def test_budget_is_shared_by_lists_and_discards_late_partial_result(self):
        client = client_responses([[{"id": "p"}], project_data("p", [task()]), project_data("inbox")])
        with patch.object(tt.time, "monotonic", side_effect=[0, 91]), self.assertRaises(tt.TickTickError):
            client.read(NOW)
        self.assertEqual(client._get.call_count, 2)

    def test_total_count_includes_later_tasks_but_not_completed_or_duplicates(self):
        later = task("later", dueDate="2027-01-01")
        client = client_responses([[{"id": "p"}], project_data("p", [task(), later, later, task("done", status=2)]), project_data("inbox")])
        result = client.read(NOW)
        self.assertEqual(result.open_task_count, 2)
        self.assertEqual(len(result.tasks), 1)


class TickTickDateTest(unittest.TestCase):
    def test_offsets_convert_to_berlin_including_midnight(self):
        t = tt.normalize_task(task(dueDate="2026-10-05T23:30:00.000+0000"), "Privat")
        self.assertEqual(t["due_date"], "2026-10-06")
        self.assertIn("01:30 (Berlin)", t["due"])
        self.assertEqual(tt.split_tasks([t], NOW)[0], [t])

    def test_all_day_keeps_calendar_date_in_task_zone_without_invented_time(self):
        for value, zone, date in [("2026-10-06T00:00:00+0900", "Asia/Tokyo", "2026-10-06"),
                                  ("2026-10-05T22:00:00+0000", "Europe/Berlin", "2026-10-06"),
                                  ("2026-10-06", "Europe/Berlin", "2026-10-06")]:
            with self.subTest(value=value):
                t = tt.normalize_task(task(dueDate=value, isAllDay=True, timeZone=zone), "Privat")
                self.assertEqual(t["due_date"], date)
                self.assertEqual(t["due_at"], "")
                self.assertIn("ganztägig", t["due"])

    def test_dst_start_end_and_explicit_repeated_hour_offsets(self):
        for value, expected in [("2026-03-29T00:30:00Z", "01:30"), ("2026-03-29T01:30:00Z", "03:30"),
                                ("2026-10-25T00:30:00Z", "02:30"), ("2026-10-25T01:30:00Z", "02:30")]:
            with self.subTest(value=value):
                t = tt.normalize_task(task(dueDate=value), "Privat")
                self.assertIn(expected, t["due"])
        first = tt.normalize_task(task(dueDate="2026-10-25T02:30:00+0200"), "Privat")
        second = tt.normalize_task(task(dueDate="2026-10-25T02:30:00+0100"), "Privat")
        self.assertNotEqual(first["due_at"], second["due_at"])

    def test_naive_dst_ambiguity_missing_hour_and_invalid_dates_fail(self):
        for value in ["2026-10-25T02:30:00", "2026-03-29T02:30:00", "not-a-date", "2026-02-30", 12345]:
            with self.subTest(value=value):
                with self.assertRaises(tt.TickTickError):
                    tt.normalize_task(task(dueDate=value), "Privat")
        with self.assertRaises(tt.TickTickError):
            tt.normalize_task(task(dueDate="2026-10-06", timeZone="Invalid/Zone"), "Privat")

    def test_year_boundary_uses_full_date_instead_of_reparsing_display_label(self):
        now = dt.datetime(2026, 12, 31, 23, tzinfo=tt.BERLIN)
        future = tt.normalize_task(task(dueDate="2027-01-01T08:00:00Z"), "Privat")
        overdue = tt.normalize_task(task("old", dueDate="2025-12-31T08:00:00Z"), "Privat")
        self.assertEqual(daily_cody.split_reminders_for_briefing([future, overdue], now), ([overdue], [future], []))

    def test_horizon_two_days_and_friday_seven_days_inclusive(self):
        for now, last_date in [(NOW, "2026-10-08"), (dt.datetime(2026, 10, 9, 6, tzinfo=tt.BERLIN), "2026-10-16")]:
            boundary = tt.normalize_task(task(dueDate=last_date), "Privat")
            after_date = (dt.date.fromisoformat(last_date) + dt.timedelta(days=1)).isoformat()
            after = tt.normalize_task(task(dueDate=after_date), "Privat")
            self.assertTrue(tt.relevant(boundary, now))
            self.assertFalse(tt.relevant(after, now))
        utc_thursday = dt.datetime(2026, 10, 8, 23, tzinfo=dt.UTC)
        self.assertTrue(tt.relevant(tt.normalize_task(task(dueDate="2026-10-16"), "Privat"), utc_thursday))

    def test_undated_and_waiting_are_kept_without_synthetic_due_date(self):
        undated = tt.normalize_task(task(), "Inbox")
        waiting = tt.normalize_task(task("wait", tags=["waiting-for"]), "Privat")
        waiting_due = tt.normalize_task(task("wd", dueDate="2026-10-05", title="Warten auf Antwort"), "Privat")
        distant_waiting = tt.normalize_task(task("future", dueDate="2027-01-01"), "Warten auf")
        self.assertEqual(tt.split_tasks([undated, waiting, waiting_due], NOW), ([], [undated], [waiting, waiting_due]))
        self.assertTrue(tt.relevant(distant_waiting, NOW))
        self.assertIn("ohne Termin", tt.format_task(undated))
        self.assertEqual(undated["due_date"], "")

    def test_start_only_is_a_planned_date_not_a_due_date(self):
        t = tt.normalize_task(task(startDate="2026-10-06T10:00:00+0200"), "Privat")
        self.assertEqual(t["due"], "")
        self.assertIn("geplant Di 6.10.2026 10:00", tt.format_task(t))
        self.assertNotIn("fällig", tt.format_task(t))


class TickTickTransportTest(unittest.TestCase):
    def client(self):
        client = tt.TickTickClient(TOKEN)
        client.deadline = __import__("time").monotonic() + 90
        client.opener = Mock()
        return client

    def response(self, payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = payload
        return response

    def http_error(self, code):
        return urllib.error.HTTPError("https://private.invalid/" + TOKEN, code,
                                      "private error " + TOKEN, {}, io.BytesIO(TOKEN.encode()))

    def test_transport_uses_only_get_fixed_host_and_bounded_timeout(self):
        c = self.client()
        c.opener.open.return_value = self.response(b"[]")
        self.assertEqual(c._get("/project?offset=0&limit=100"), [])
        req = c.opener.open.call_args.args[0]
        self.assertEqual(req.get_method(), "GET")
        self.assertTrue(req.full_url.startswith(tt.API + "/project"))
        self.assertEqual(req.get_header("Authorization"), "Bearer " + TOKEN)
        self.assertLessEqual(c.opener.open.call_args.kwargs["timeout"], 15)
        self.assertIsNone(req.data)

    def test_401_403_and_redirect_do_not_retry_or_leak_credentials(self):
        for code in [401, 403, 302, 404]:
            c = self.client()
            c.opener.open.side_effect = self.http_error(code)
            with self.subTest(code=code), self.assertRaises(tt.TickTickError) as raised, patch.object(tt.time, "sleep") as sleep:
                c._get("/project")
            self.assertNotIn(TOKEN, str(raised.exception))
            self.assertEqual(c.opener.open.call_count, 1)
            sleep.assert_not_called()
        self.assertIsNone(tt.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))

    def test_429_5xx_and_network_errors_retry_and_recover(self):
        for error in [self.http_error(429), self.http_error(503), urllib.error.URLError(TOKEN), TimeoutError(TOKEN), tt.http.client.IncompleteRead(b"private", 100)]:
            c = self.client()
            c.opener.open.side_effect = [error, self.response(b"[]")]
            with self.subTest(error=type(error).__name__), patch.object(tt.time, "sleep") as sleep:
                self.assertEqual(c._get("/project"), [])
            self.assertEqual(c.opener.open.call_count, 2)
            sleep.assert_called_once_with(1)

    def test_exhausted_retries_are_sanitized(self):
        c = self.client()
        c.opener.open.side_effect = urllib.error.URLError(TOKEN)
        with patch.object(tt.time, "sleep"), self.assertRaises(tt.TickTickError) as raised:
            c._get("/project")
        self.assertEqual(c.opener.open.call_count, 3)
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_deadline_prevents_request_sleep_and_late_success(self):
        c = self.client()
        with patch.object(tt.time, "monotonic", return_value=c.deadline), self.assertRaises(tt.TickTickError):
            c._get("/project")
        c.opener.open.assert_not_called()
        c.opener.open.side_effect = urllib.error.URLError(TOKEN)
        with patch.object(tt.time, "monotonic", return_value=c.deadline - 0.5), patch.object(tt.time, "sleep") as sleep, self.assertRaises(tt.TickTickError):
            c._get("/project")
        sleep.assert_not_called()
        c.opener.open.side_effect = None
        c.opener.open.return_value = self.response(b"[]")
        with patch.object(tt.time, "monotonic", side_effect=[c.deadline - 1, c.deadline + 1]), self.assertRaises(tt.TickTickError):
            c._get("/project")

    def test_invalid_json_and_size_limit_are_not_successful_empty_lists(self):
        for payload in [b"broken " + TOKEN.encode(), b"\xff", b"x" * (tt.MAX_RESPONSE_BYTES + 1)]:
            c = self.client()
            c.opener.open.return_value = self.response(payload)
            with self.subTest(size=len(payload)), self.assertRaises(tt.TickTickError) as raised:
                c._get("/project")
            self.assertNotIn(TOKEN, str(raised.exception))

    def test_missing_token_warns_without_network(self):
        with patch.object(tt.urllib.request, "build_opener") as opener:
            result = tt.read_tasks("", NOW)
        opener.assert_not_called()
        self.assertIn("nicht eingerichtet", result.warning)

    def test_invalid_time_limits_do_not_reach_network(self):
        for settings in [{"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
                         {"total_timeout_seconds": float("inf")}, {"total_timeout_seconds": -1}]:
            with self.subTest(settings=settings), patch.object(tt.urllib.request, "build_opener") as opener:
                result = tt.read_tasks(TOKEN, NOW, **settings)
            self.assertFalse(result.status()["available"])
            opener.assert_not_called()


class TickTickBriefingTest(unittest.TestCase):
    def build(self, tasks, warning=None, available=True, application=None):
        with patch.object(daily_cody.follow_up_snapshot, "read_snapshot", return_value=(None, "fehlt")):
            return daily_cody.build_briefing(SimpleNamespace(openai_api_key=None), NOW,
                {"summary": "DWD-Messwerte"}, [], [], [], tasks, warning,
                application or {}, [], [], [], [], tasks_status={"source": "TickTick", "available": available,
                "fetched_at": "2026-10-06T06:01:00+02:00"})

    def test_full_lists_ids_dates_and_waiting_render_in_correct_sections(self):
        tasks = [tt.normalize_task(task(str(i), dueDate="2026-10-06", title="Gleicher Titel"), "Privat") for i in range(15)]
        tasks += [tt.normalize_task(task("wait"+str(i), title="Warten auf gleiche Antwort"), "Privat") for i in range(15)]
        tasks += [tt.normalize_task(task("undated"), "Inbox"), tt.normalize_task(task("later", dueDate="2026-10-07"), "Privat")]
        result = self.build(tasks)
        self.assertEqual(result.count("Gleicher Titel"), 15)
        self.assertEqual(result.count("Warten auf gleiche Antwort"), 15)
        self.assertEqual(result.count("https://ticktick.com/"), 32)
        self.assertIn("frisch gelesen 06.10.2026 06:01", result)
        self.assertIn("Aufgabe undated (Inbox) — ohne Termin", result)
        self.assertIn("fällig Mi 7.10.2026 (ganztägig)", result)

    def test_failed_fetch_does_not_claim_no_tasks_or_restore_old_application_tasks(self):
        application = {"waiting_for": [{"subject": "Alte Bewerbung", "detail": "alte Aufgabe"}]}
        result = self.build([], "TickTick fehlgeschlagen", False, application)
        self.assertIn("TickTick-Aufgabenstand unbekannt", result)
        self.assertIn("TickTick fehlgeschlagen", result)
        self.assertNotIn("Keine faelligen Aufgaben", result)
        self.assertNotIn("Alte Bewerbung", result)
        self.assertNotIn("frisch gelesen", result)

    def test_successful_empty_read_has_provenance_and_is_distinct_from_failure(self):
        result = self.build([])
        self.assertIn("inklusive Inbox", result)
        self.assertIn("Keine faelligen Aufgaben", result)
        self.assertNotIn("Aufgabenstand unbekannt", result)
        self.assertNotIn("## Reminders", result)

    def test_ai_cannot_invent_upcoming_tasks_or_remove_undated_tasks(self):
        t = tt.normalize_task(task(), "Inbox")
        context = {"weather": {"summary": "Wetter"}, "world_cup_games": [], "deliveries": [],
                   "today_todos": [], "reminders": [t], "yesterday_open_mail": [], "waiting_for": []}
        result = daily_cody.finalize_briefing("# Cody\n## Reminders\n- Erfunden\n## Deliveries\n- Paket", context)
        self.assertNotIn("Erfunden", result)
        self.assertIn("Aufgabe a (Inbox) — ohne Termin", result)

    def test_empty_task_source_removes_invented_ai_reminder_section(self):
        context = {"weather": {"summary": "Wetter"}, "world_cup_games": [], "deliveries": [],
                   "today_todos": [], "reminders": [], "yesterday_open_mail": [], "waiting_for": []}
        result = daily_cody.finalize_briefing("# Cody\n## Reminders\n- Erfunden\n## Deliveries\n- Paket", context)
        self.assertNotIn("Erfunden", result)
        self.assertNotIn("## Reminders", result)

    def test_old_application_overrides_cannot_hide_current_ticktick_tasks(self):
        t = tt.normalize_task(task(title="Aktuelle Bewerbung", dueDate="2026-10-06"), "Privat")
        application = {"action_overrides": [{"topic": "Aktuelle Bewerbung", "action": "closed"}],
                       "waiting_for": [{"subject": "Alte Bewerbung", "detail": "Alt"}]}
        with patch.object(daily_cody, "load_resolved_topic_overrides", return_value=application["action_overrides"]):
            result = self.build([t], application=application)
        self.assertIn("Aktuelle Bewerbung", result)
        self.assertNotIn("Alte Bewerbung", result)

    def test_preflight_reports_only_counts_and_errors_never_task_text(self):
        import importlib.util
        path = Path(__file__).resolve().parents[1] / "scripts/check_ticktick_access.py"
        spec = importlib.util.spec_from_file_location("ticktick_preflight", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = tt.TaskRead(tasks=[{"title": "SYNTHETIC PRIVATE TITLE"}], fetched_at=NOW.isoformat(), project_count=2, open_task_count=3)
        with patch.object(tt, "read_tasks", return_value=result), patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(module.main(), 0)
        self.assertIn("3 total open tasks", out.getvalue())
        self.assertNotIn("SYNTHETIC PRIVATE TITLE", out.getvalue())
        with patch.object(tt, "read_tasks", return_value=tt.TaskRead(warning="nicht verfügbar")), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(module.main(), 1)

    def test_markdown_and_html_in_task_text_are_safe(self):
        t = tt.normalize_task(task(title="[Fake](https://evil.invalid) <script>alert(1)</script>", content="Zeile\n## Deliveries"), "Privat")
        result = daily_cody.markdown_to_basic_html(self.build([t]))
        self.assertNotIn("<script>", result)
        self.assertNotIn('href="https://evil.invalid"', result)

    def test_main_reads_ticktick_after_mail_and_never_reads_or_refreshes_apple(self):
        config = daily_cody.Config("from@example.org", "to@example.org", "Europe/Berlin", [], "", "", "", "", "", "",
            None, "", 1, 1, False, 6, 9, True, False, True, False, False, False, 1, "", False, "", 1, "",
            ticktick_access_token=TOKEN)
        events = []
        def fetched(*args, **kwargs):
            events.append("ticktick")
            return tt.TaskRead(fetched_at=NOW.isoformat(), project_count=1)
        with patch.object(daily_cody, "load_config", return_value=config), patch.object(daily_cody, "refresh_google_token", return_value="synthetic-google"), \
             patch.object(daily_cody, "already_sent_today", return_value=False), patch.object(daily_cody, "get_weather", return_value={"summary": "Wetter"}), \
             patch.object(daily_cody, "list_calendar_events", return_value=([], [])), patch.object(daily_cody, "list_world_cup_games", return_value=[]), \
             patch.object(daily_cody, "list_recent_mail", return_value=[]), patch.object(daily_cody, "list_delivery_mail", return_value=[]), \
             patch.object(daily_cody, "list_yesterday_open_mail", return_value=[]), patch.object(daily_cody, "list_waiting_for_mail", side_effect=lambda *args: events.append("mail") or []), \
             patch.object(tt, "read_tasks", side_effect=fetched) as fetch, patch.object(daily_cody, "read_exported_reminders") as apple, \
             patch.object(daily_cody, "refresh_reminders_export") as refresh, patch.object(daily_cody, "read_application_wiki_snapshot") as wiki, \
             patch.object(daily_cody, "send_email") as send, patch.object(sys, "argv", ["daily_cody.py"]):
            self.assertEqual(daily_cody.main(), 0)
        self.assertEqual(events, ["mail", "ticktick"])
        fetch.assert_called_once()
        apple.assert_not_called()
        refresh.assert_not_called()
        wiki.assert_not_called()
        send.assert_not_called()
        self.assertNotIn(TOKEN, repr(config))


if __name__ == "__main__":
    unittest.main()
