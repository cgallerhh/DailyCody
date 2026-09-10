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
    def setUp(self):
        daily_cody.reset_gmail_request_state()

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

    def test_gmail_rate_limit_retries_and_then_returns_response(self):
        url = f"{daily_cody.GMAIL_API}/messages/example?format=full"
        error_payload = {
            "error": {
                "code": 403,
                "errors": [{"reason": "rateLimitExceeded"}],
            }
        }
        http_error = urllib.error.HTTPError(
            url,
            403,
            "Forbidden",
            None,
            io.BytesIO(json.dumps(error_payload).encode("utf-8")),
        )
        success = io.BytesIO(b'{"id": "example"}')

        with mock.patch(
            "daily_cody.urllib.request.urlopen", side_effect=[http_error, success]
        ) as urlopen, mock.patch("daily_cody.reserve_gmail_quota"), mock.patch(
            "daily_cody.time.sleep"
        ) as sleep, mock.patch.object(
            daily_cody.sys, "stderr", new=io.StringIO()
        ):
            result = daily_cody.request_json(url, token="redacted-test-token")

        self.assertEqual(result, {"id": "example"})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(2)

    def test_persistent_gmail_rate_limit_still_fails_closed(self):
        message_id = "sensitive-message-id"
        url = f"{daily_cody.GMAIL_API}/messages/{message_id}?format=full"

        def rate_limit_error():
            payload = {
                "error": {
                    "code": 403,
                    "errors": [{"reason": "rateLimitExceeded"}],
                }
            }
            return urllib.error.HTTPError(
                url,
                403,
                "Forbidden",
                None,
                io.BytesIO(json.dumps(payload).encode("utf-8")),
            )

        errors = [
            rate_limit_error()
            for _ in range(len(daily_cody.GMAIL_RATE_LIMIT_RETRY_DELAYS_SECONDS) + 1)
        ]
        with mock.patch(
            "daily_cody.urllib.request.urlopen", side_effect=errors
        ) as urlopen, mock.patch("daily_cody.reserve_gmail_quota"), mock.patch(
            "daily_cody.time.sleep"
        ) as sleep, mock.patch.object(
            daily_cody.sys, "stderr", new=io.StringIO()
        ):
            with self.assertRaises(RuntimeError) as raised:
                daily_cody.request_json(url, token="redacted-test-token")

        self.assertEqual(urlopen.call_count, len(errors))
        self.assertEqual(
            [call.args[0] for call in sleep.call_args_list],
            list(daily_cody.GMAIL_RATE_LIMIT_RETRY_DELAYS_SECONDS),
        )
        self.assertIn("reason=rateLimitExceeded", str(raised.exception))
        self.assertNotIn(message_id, str(raised.exception))

    def test_gmail_quota_costs_match_used_api_methods(self):
        self.assertEqual(
            daily_cody.gmail_request_quota_units(f"{daily_cody.GMAIL_API}/messages"),
            5,
        )
        self.assertEqual(
            daily_cody.gmail_request_quota_units(
                f"{daily_cody.GMAIL_API}/messages/example?format=full"
            ),
            20,
        )
        self.assertEqual(
            daily_cody.gmail_request_quota_units(
                f"{daily_cody.GMAIL_API}/threads/example?format=metadata"
            ),
            40,
        )
        self.assertEqual(
            daily_cody.gmail_request_quota_units(f"{daily_cody.GMAIL_API}/messages/send"),
            100,
        )

    def test_gmail_get_response_is_cached_for_the_process(self):
        url = f"{daily_cody.GMAIL_API}/messages?maxResults=1"
        response = io.BytesIO(b'{"messages": [{"id": "example"}]}')

        with mock.patch("daily_cody.urllib.request.urlopen", return_value=response) as urlopen:
            first = daily_cody.request_json(url, token="redacted-test-token")
            second = daily_cody.request_json(url, token="redacted-test-token")

        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        urlopen.assert_called_once()

    def test_gmail_quota_guard_waits_for_a_rolling_window(self):
        daily_cody._gmail_quota_events.append((0.0, 20))

        with mock.patch.object(
            daily_cody, "GMAIL_QUOTA_SAFE_UNITS_PER_WINDOW", 20
        ), mock.patch(
            "daily_cody.time.monotonic", side_effect=[10.0, 60.3]
        ), mock.patch(
            "daily_cody.time.sleep"
        ) as sleep, mock.patch.object(
            daily_cody.sys, "stderr", new=io.StringIO()
        ):
            daily_cody.reserve_gmail_quota(20, "messages.get")

        sleep.assert_called_once_with(50.25)
        self.assertEqual(daily_cody._gmail_quota_events, [(60.3, 20)])

    def test_gmail_quota_guard_smooths_request_bursts(self):
        interval = 20 / daily_cody.GMAIL_QUOTA_SAFE_UNITS_PER_SECOND

        with mock.patch(
            "daily_cody.time.monotonic", side_effect=[0.0, 0.1, interval + 0.01]
        ), mock.patch("daily_cody.time.sleep") as sleep:
            daily_cody.reserve_gmail_quota(20, "messages.get")
            daily_cody.reserve_gmail_quota(20, "messages.get")

        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], interval - 0.1)

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
