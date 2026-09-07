import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


class MailTriageTest(unittest.TestCase):
    def test_gmail_403_reports_reason_without_message_id(self):
        message_id = "sensitive-message-id"
        url = f"{daily_cody.GMAIL_API}/messages/{message_id}?format=full"
        error_payload = {
            "error": {
                "code": 403,
                "errors": [{"reason": "insufficientPermissions"}],
                "status": "PERMISSION_DENIED",
            }
        }
        http_error = urllib.error.HTTPError(
            url,
            403,
            "Forbidden",
            None,
            io.BytesIO(json.dumps(error_payload).encode("utf-8")),
        )

        with mock.patch("daily_cody.urllib.request.urlopen", side_effect=http_error):
            with self.assertRaises(RuntimeError) as raised:
                daily_cody.request_json(url, token="redacted-test-token")

        message = str(raised.exception)
        self.assertEqual(
            message,
            "Gmail API messages.get failed with HTTP 403 (reason=insufficientPermissions)",
        )
        self.assertNotIn(message_id, message)

    def test_adesso_payroll_reply_is_actionable(self):
        subject = "RE: Gehaltsabrechnungen Christian Galler-114293"
        snippet = (
            "Hallo Christian, wir werden die korrigierten Daten an die Agentur für Arbeit "
            "übermitteln und die fehlenden Unterlagen nachreichen. Hast du einen festen "
            "Ansprechpartner bei der Agentur für Arbeit?"
        )

        self.assertTrue(daily_cody.looks_actionable(subject, snippet))


if __name__ == "__main__":
    unittest.main()
