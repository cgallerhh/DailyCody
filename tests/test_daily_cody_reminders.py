import datetime as dt
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


NOW = dt.datetime(2026, 7, 1, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def reminder_config(
    export_path: Path,
    *,
    require_fresh: bool = True,
    refresh_stale: bool = False,
    refresh_command: str = "",
    max_age_hours: int = 1,
) -> daily_cody.Config:
    return daily_cody.Config(
        sender="sender@example.com",
        recipient="recipient@example.com",
        timezone="Europe/Berlin",
        calendar_names=[],
        weather_latitude="",
        weather_longitude="",
        weather_label="",
        google_client_id="",
        google_client_secret="",
        google_refresh_token="",
        openai_api_key=None,
        openai_model="gpt-5.5",
        openai_timeout_seconds=120,
        openai_max_attempts=2,
        allow_template_fallback=False,
        send_window_hour=6,
        send_window_end_hour=9,
        force_send=True,
        allow_duplicate=True,
        dry_run=True,
        include_undated_reminders=False,
        require_fresh_reminders=require_fresh,
        fail_on_stale_reminders=False,
        reminders_max_age_hours=max_age_hours,
        reminders_export_path=str(export_path),
        refresh_stale_reminders=refresh_stale,
        reminders_refresh_command=refresh_command,
        reminders_refresh_timeout_seconds=10,
        application_wiki_snapshot_path="data/application_wiki_snapshot.json",
    )


class AppleRemindersTest(unittest.TestCase):
    def test_reminder_parser_uses_list_name_and_remind_me_date(self):
        reminder = daily_cody.normalize_exported_reminder(
            {
                "name": "Agniesza: 40 EUR da?",
                "list_name": "_INBOX",
                "remind_me_date": "2026-07-01T09:30:00",
                "completed": False,
            },
            NOW,
        )

        self.assertEqual(reminder["title"], "Agniesza: 40 EUR da?")
        self.assertEqual(reminder["list"], "_INBOX")
        self.assertEqual(reminder["due"], "Mi 1.7")

    def test_reminder_parser_falls_back_to_alarm_dates(self):
        reminder = daily_cody.normalize_exported_reminder(
            {
                "title": "Nur Alarm, kein Due-Date",
                "calendarName": "Privat",
                "alarms": [{"absolute_date": "2026-07-01T18:00:00"}],
                "completed": False,
            },
            NOW,
        )

        self.assertEqual(reminder["title"], "Nur Alarm, kein Due-Date")
        self.assertEqual(reminder["list"], "Privat")
        self.assertEqual(reminder["due"], "Mi 1.7")

    def test_read_exported_reminders_refreshes_stale_local_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "reminders.json"
            status_path = tmp_path / "reminders_export_status.json"
            refresh_script = tmp_path / "refresh_reminders.py"
            export_path.write_text("[]\n", encoding="utf-8")
            status_path.write_text(
                json.dumps({"exported_at": "2026-06-27T06:00:00Z"}),
                encoding="utf-8",
            )
            refresh_script.write_text(
                textwrap.dedent(
                    f"""
                    import json
                    from pathlib import Path

                    Path({str(export_path)!r}).write_text(json.dumps([
                        {{
                            "name": "Heute offen",
                            "list_name": "_INBOX",
                            "due_date": "2026-07-01T00:00:00",
                            "completed": False
                        }}
                    ]), encoding="utf-8")
                    Path({str(status_path)!r}).write_text(json.dumps({{
                        "exported_at": "2026-07-01T06:00:00Z"
                    }}), encoding="utf-8")
                    """
                ),
                encoding="utf-8",
            )
            original_status_path = daily_cody.REMINDERS_EXPORT_STATUS_PATH
            daily_cody.REMINDERS_EXPORT_STATUS_PATH = status_path
            try:
                reminders, warning = daily_cody.read_exported_reminders(
                    reminder_config(
                        export_path,
                        refresh_stale=True,
                        refresh_command=f"{sys.executable} {refresh_script}",
                    ),
                    NOW,
                )
            finally:
                daily_cody.REMINDERS_EXPORT_STATUS_PATH = original_status_path

        self.assertIsNone(warning)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["title"], "Heute offen")
        self.assertEqual(reminders[0]["due"], "Mi 1.7")

    def test_required_fresh_reminders_are_skipped_when_refresh_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            export_path = tmp_path / "reminders.json"
            status_path = tmp_path / "reminders_export_status.json"
            export_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "Heute offen",
                            "list_name": "_INBOX",
                            "due_date": "2026-07-01T00:00:00",
                            "completed": False,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            status_path.write_text(
                json.dumps({"exported_at": "2026-06-27T06:00:00Z"}),
                encoding="utf-8",
            )
            original_status_path = daily_cody.REMINDERS_EXPORT_STATUS_PATH
            daily_cody.REMINDERS_EXPORT_STATUS_PATH = status_path
            try:
                reminders, warning = daily_cody.read_exported_reminders(
                    reminder_config(export_path, refresh_stale=False),
                    NOW,
                )
            finally:
                daily_cody.REMINDERS_EXPORT_STATUS_PATH = original_status_path

        self.assertEqual(reminders, [])
        self.assertIn("Apple Reminders skipped", warning)


if __name__ == "__main__":
    unittest.main()
