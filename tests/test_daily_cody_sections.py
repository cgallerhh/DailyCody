import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


class BriefingSectionsTest(unittest.TestCase):
    def test_factual_sections_replace_incomplete_ai_output(self):
        context = {
            "weather": {"summary": "DWD-Messwerte für heute."},
            "world_cup_games": [],
            "deliveries": [],
            "today_todos": [{"title": "Rechnung prüfen", "notes": "bis heute"}],
            "yesterday_open_mail": [],
            "waiting_for": [{"source": "reminder", "subject": "Antwort von adesso", "due": "29.09."}],
        }
        ai_output = (
            "# Daily Cody\n\n## Today\n- Wetter\n\n"
            "## Today's to-dos\n- Nichts offen.\n\n"
            "## Waiting for...\n- Keine Rückmeldungen.\n\n"
            "## Deliveries\n- Paket ausgedacht.\n"
        )

        result = daily_cody.finalize_briefing(ai_output, context)

        self.assertIn("- Rechnung prüfen — bis heute", result)
        self.assertIn("- 29.09. — Antwort von adesso — nachfassen.", result)
        self.assertIn("- Keine offenen Liefer- oder Bestellmails gefunden.", result)
        self.assertNotIn("Nichts offen.", result)
        self.assertNotIn("Keine Rückmeldungen.", result)
        self.assertNotIn("Paket ausgedacht.", result)
        self.assertEqual(result.count("DWD-Messwerte für heute."), 1)

    def test_missing_todo_and_waiting_sections_are_inserted_before_deliveries(self):
        briefing = "# Daily Cody\n\n## Today\n- Wetter\n\n## Deliveries\n- Noch nichts."
        context = {
            "weather": {"summary": "DWD-Messwerte für heute."},
            "world_cup_games": [],
            "deliveries": [],
            "today_todos": [{"title": "Aufgabe A"}],
            "yesterday_open_mail": [],
            "waiting_for": [],
        }

        result = daily_cody.finalize_briefing(briefing, context)

        self.assertLess(result.index("## Today's to-dos"), result.index("## Waiting for..."))
        self.assertLess(result.index("## Waiting for..."), result.index("## Deliveries"))
        self.assertIn("- Aufgabe A", result)


if __name__ == "__main__":
    unittest.main()
