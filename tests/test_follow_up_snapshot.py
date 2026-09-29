import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import daily_cody
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

    def read(self):
        return follow_up_snapshot.read_snapshot(self.now, json.dumps(self.snapshot))

    def test_fresh_complete_snapshot_is_source_backed_and_independent_of_ai_output(self):
        snapshot, warning = self.read()
        self.assertIsNone(warning)
        with patch.object(follow_up_snapshot, "read_snapshot", return_value=(snapshot, warning)):
            result = daily_cody.build_briefing(
                SimpleNamespace(openai_api_key=None), self.now, {"summary": "DWD-Wetter"},
                [], [], [], [], None, {}, [], [], [], [],
            )
        self.assertIn("## Follow-up", result)
        self.assertIn("Eine Antwort ist eingegangen.", result)
        self.assertIn("[Quelle](https://mail.google.com/", result)
        self.assertLess(result.index("## Follow-up"), result.index("## Waiting for..."))

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
