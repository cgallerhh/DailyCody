import datetime as dt
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody
import follow_up_snapshot


class RetiredFollowUpTest(unittest.TestCase):
    def build(self):
        return daily_cody.build_briefing(
            SimpleNamespace(openai_api_key=None), dt.datetime.fromisoformat("2026-10-13T06:00:00+02:00"),
            {"summary": "DWD-Messwerte"}, [], [], [], [{"title": "Aufgabe behalten", "due": "2026-10-13"}],
            "Erinnerungen sind veraltet", {}, [], [], [], [],
        )

    def test_snapshot_never_read_and_other_data_warning_preserved(self):
        for encoded in ("", "not json", '{"status":"complete","items":[]}'):
            with self.subTest(encoded=encoded), patch.dict(os.environ, {
                "FOLLOW_UP_SNAPSHOT_JSON": encoded, "FOLLOW_UP_SNAPSHOT_PATH": "/missing/snapshot.json",
                "CODY_GENERATION_MODE": "source",
            }), patch.object(follow_up_snapshot, "read_snapshot", side_effect=AssertionError("must not read")) as read:
                result = self.build()
                rendered = daily_cody.markdown_to_basic_html(result)
                read.assert_not_called()
                for output in (result, rendered):
                    self.assertNotIn("Follow-up", output)
                    self.assertNotIn("Follow-Up-Monitor", output)
                    self.assertNotIn("Gmail live", output)
                    self.assertIn("Erinnerungen sind veraltet", output)
                    self.assertIn("Aufgabe behalten", output)
                    self.assertIn("Waiting for", output)
                    self.assertIn("Deliveries", output)

    def test_ai_section_removed_without_removing_adjacent_sections(self):
        context = {"weather": {"summary": "Messwerte"}, "world_cup_games": [], "deliveries": [],
                   "today_todos": [], "yesterday_open_mail": [], "waiting_for": [],
                   "follow_up_lines": ["- Old snapshot must not return"]}
        for title in ("Follow-up", "Follow ups"):
            with self.subTest(title=title):
                ai_output = ("# Cody\n## Today\n- Wetter\n## Reminders\n- Behalten\n"
                             f"## {title}\n- Hinweis: Follow-Up-Monitor: aktuelle Ergebnisse sind nicht verfügbar.\n"
                             "## Approaching\n- Termin behalten\n")
                result = daily_cody.finalize_briefing(ai_output, context)
                rendered = daily_cody.markdown_to_basic_html(result)
                self.assertNotIn(title, result)
                self.assertNotIn("Follow-Up-Monitor", rendered)
                self.assertNotIn("Old snapshot", result)
                self.assertIn("Behalten", result)
                self.assertIn("Termin behalten", result)


if __name__ == "__main__":
    unittest.main()
