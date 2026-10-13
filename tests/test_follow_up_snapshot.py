import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import follow_up_snapshot
import publish_follow_up_snapshot


class FollowUpSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 9, 29, 6, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        self.snapshot = {
            "schema_version": 1,
            "status": "complete",
            "checked_from": "2026-09-28T05:00:00+02:00",
            "checked_until": "2026-09-29T05:01:00+02:00",
            "generated_at": "2026-09-29T05:02:00+02:00",
            "coverage": {key: "ok" for key in follow_up_snapshot.SOURCE_KINDS},
            "items": [{
                "id": "example-reply",
                "topic": "Beispiel",
                "change": "Eine Antwort ist eingegangen.",
                "relevance": "Die Rueckfrage ist noch offen.",
                "action": "Antwort pruefen.",
                "observed_at": "2026-09-28T10:00:00+02:00",
                "expires_at": "2026-10-02T18:00:00+02:00",
                "sources": [{"kind": "gmail", "id": "message-1", "from": "Person <person@example.org>", "url": "https://mail.google.com/mail/u/0/#all/message-1"}],
            }],
        }
        clock = Mock(wraps=dt.datetime)
        clock.now.return_value = self.now.astimezone(dt.timezone.utc)
        self.clock = clock
        patcher = patch.object(publish_follow_up_snapshot, "dt", SimpleNamespace(datetime=clock, timezone=dt.timezone))
        patcher.start()
        self.addCleanup(patcher.stop)

    def read(self):
        return follow_up_snapshot.read_snapshot(self.now, json.dumps(self.snapshot))

    def test_missed_weekday_check_is_rejected(self):
        self.snapshot["checked_until"] = "2026-09-28T05:01:00+02:00"
        self.snapshot["items"][0]["observed_at"] = "2026-09-28T04:00:00+02:00"
        snapshot, warning = self.read()
        self.assertIsNone(snapshot)
        self.assertIn("veraltet", warning)

    def test_weekend_uses_friday_with_visible_timestamp_but_monday_requires_new_check(self):
        self.snapshot["checked_until"] = "2026-10-02T05:01:00+02:00"
        self.snapshot["generated_at"] = "2026-10-02T05:02:00+02:00"
        self.now = dt.datetime(2026, 10, 4, 6, 0, tzinfo=self.now.tzinfo)
        snapshot, warning = self.read()
        self.assertIsNone(warning)
        self.assertIn("02.10.2026, 05:01", " ".join(follow_up_snapshot.format_items(snapshot, warning, self.now)))
        self.now += dt.timedelta(days=1)
        self.assertIn("veraltet", self.read()[1])

    def test_expired_event_is_removed_without_mutating_source(self):
        self.snapshot["items"][0]["expires_at"] = "2026-09-28T18:00:00+02:00"
        snapshot, warning = self.read()
        self.assertIsNone(warning)
        self.assertEqual(snapshot["items"], [])
        self.assertEqual(len(self.snapshot["items"]), 1)

    def test_incomplete_sources_future_dates_and_unsafe_links_are_rejected(self):
        for field, value in (("coverage", {"gmail": "ok"}), ("generated_at", "2026-10-01T05:00:00+02:00")):
            with self.subTest(field=field), patch.dict(self.snapshot, {field: value}):
                self.assertIsNone(self.read()[0])
        self.snapshot["items"][0]["sources"][0]["url"] = "https://example.com/collect"
        self.assertIsNone(self.read()[0])

    def test_missing_and_invalid_data_never_claim_successful_empty_check(self):
        for encoded in (None, "", "{}", "null", "[]", "not json"):
            with self.subTest(encoded=encoded):
                snapshot, warning = follow_up_snapshot.read_snapshot(self.now, encoded)
                self.assertIsNone(snapshot)
                self.assertIsNotNone(warning)
                lines = " ".join(follow_up_snapshot.format_items(snapshot, warning, self.now))
                self.assertIn("Hinweis", lines)
                self.assertNotIn("Geprüft", lines)
                self.assertNotIn("Keine aktuell relevanten", lines)

    def test_successful_empty_snapshot_shows_verified_time(self):
        self.snapshot["items"] = []
        snapshot, warning = self.read()
        self.assertIsNone(warning)
        lines = " ".join(follow_up_snapshot.format_items(snapshot, warning, self.now))
        self.assertIn("Geprüft", lines)
        self.assertIn("Keine aktuell relevanten", lines)

    def test_null_expiry_from_local_monitor_is_not_a_valid_handoff(self):
        self.snapshot["items"][0]["expires_at"] = None
        snapshot, warning = self.read()
        self.assertIsNone(snapshot)
        self.assertIn("Zeitstempel fehlt", warning)

    def test_utc_caller_still_requires_monday_berlin_check(self):
        self.snapshot.update(checked_until="2026-10-09T05:01:00+02:00", generated_at="2026-10-09T05:02:00+02:00")
        self.now = dt.datetime(2026, 10, 12, 4, 0, tzinfo=dt.timezone.utc)
        self.assertIn("veraltet", self.read()[1])

    def test_schedule_uses_berlin_before_five_and_after_dst_change(self):
        for now, expected in [
            ("2026-10-12T02:00:00+00:00", "2026-10-09T05:00:00+02:00"),
            ("2026-10-26T05:00:00+00:00", "2026-10-26T05:00:00+01:00"),
        ]:
            with self.subTest(now=now):
                self.assertEqual(follow_up_snapshot.latest_scheduled_check(dt.datetime.fromisoformat(now)), dt.datetime.fromisoformat(expected))

    def test_rejected_candidate_never_publishes_or_advances_baseline(self):
        for problem in ("stale", "partial", "older"):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                candidate = Path(directory) / "candidate.json"
                baseline = Path(directory) / "baseline.json"
                original = {"checked_until": "2026-09-29T05:30:00+02:00" if problem == "older" else self.snapshot["checked_from"]}
                baseline.write_text(json.dumps(original))
                payload = json.loads(json.dumps(self.snapshot))
                if problem == "stale":
                    self.clock.now.return_value = self.now + dt.timedelta(days=1)
                else:
                    self.clock.now.return_value = self.now
                if problem == "partial":
                    payload["coverage"]["outlook_mail"] = "failed"
                candidate.write_text(json.dumps(payload))
                with patch.object(publish_follow_up_snapshot.subprocess, "run") as command:
                    with self.assertRaises(ValueError):
                        publish_follow_up_snapshot.publish(candidate, baseline, "owner/repo")
                command.assert_not_called()
                self.assertEqual(json.loads(baseline.read_text()), original)

    def test_check_only_does_not_publish_or_create_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            baseline = Path(directory) / "baseline.json"
            candidate.write_text(json.dumps(self.snapshot))
            with patch.object(sys, "argv", ["publish", "--snapshot", str(candidate), "--baseline", str(baseline), "--check-only"]), patch.object(publish_follow_up_snapshot.subprocess, "run") as command:
                self.assertEqual(publish_follow_up_snapshot.main(), 0)
            command.assert_not_called()
            self.assertFalse(baseline.exists())

    def test_local_comparison_state_is_not_sent_to_github(self):
        self.snapshot["calendar_state"] = {"private-local-marker": "comparison only"}
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            baseline = Path(directory) / "baseline.json"
            candidate.write_text(json.dumps(self.snapshot))
            with patch.object(publish_follow_up_snapshot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as command:
                publish_follow_up_snapshot.publish(candidate, baseline, "owner/repo")
            self.assertNotIn("calendar_state", json.loads(command.call_args.kwargs["input"]))
            self.assertEqual(json.loads(baseline.read_text())["calendar_state"], self.snapshot["calendar_state"])

    def test_handoff_allows_only_declared_nested_fields(self):
        self.snapshot["coverage"]["local_detail"] = "local only"
        self.snapshot["items"][0]["private_note"] = "local only"
        source = self.snapshot["items"][0]["sources"][0]
        source["full_body"] = "local only"
        source["headers"] = {"Auto-Submitted": "no", "X-Local-Detail": "local only"}
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            baseline = Path(directory) / "baseline.json"
            candidate.write_text(json.dumps(self.snapshot))
            with patch.object(publish_follow_up_snapshot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as command:
                publish_follow_up_snapshot.publish(candidate, baseline, "owner/repo")
            encoded = command.call_args.kwargs["input"]
            self.assertNotIn("local only", encoded)
            remote_source = json.loads(encoded)["items"][0]["sources"][0]
            self.assertEqual(remote_source["headers"], {"auto-submitted": "no"})
            self.assertEqual(json.loads(baseline.read_text()), self.snapshot)

    def test_timeout_does_not_advance_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            baseline = Path(directory) / "baseline.json"
            candidate.write_text(json.dumps(self.snapshot))
            # Use a valid old baseline, and preserve its exact bytes on uncertainty.
            original = json.dumps({"checked_until": self.snapshot["checked_from"]})
            baseline.write_text(original)
            with patch.object(publish_follow_up_snapshot.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 90)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    publish_follow_up_snapshot.publish(candidate, baseline, "owner/repo")
            self.assertEqual(baseline.read_text(), original)

    def test_failed_publication_does_not_advance_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            baseline = Path(directory) / "baseline.json"
            candidate.write_text(json.dumps(self.snapshot))
            original = {"checked_until": self.snapshot["checked_from"]}
            baseline.write_text(json.dumps(original))
            with patch.object(publish_follow_up_snapshot.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
                with self.assertRaises(RuntimeError):
                    publish_follow_up_snapshot.publish(candidate, baseline, "owner/repo")
            self.assertEqual(json.loads(baseline.read_text()), original)

    def test_successful_publication_uses_secret_stdin_then_saves_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.json"
            baseline = Path(directory) / "baseline.json"
            candidate.write_text(json.dumps(self.snapshot))
            with patch.object(publish_follow_up_snapshot.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as command:
                publish_follow_up_snapshot.publish(candidate, baseline, "owner/repo")
            self.assertEqual(json.loads(baseline.read_text()), self.snapshot)
            self.assertIn("secret", command.call_args.args[0])
            self.assertEqual(json.loads(command.call_args.kwargs["input"]), self.snapshot)
            self.assertNotIn("Eine Antwort", " ".join(command.call_args.args[0]))


if __name__ == "__main__":
    unittest.main()
