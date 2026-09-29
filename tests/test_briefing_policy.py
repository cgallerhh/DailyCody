import base64
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody
import delivery_detection
import mail_policy
import mail_text

NOW = dt.datetime(2026, 9, 29, 21, tzinfo=ZoneInfo("Europe/Berlin"))


def mime_message(sender, body, when, **headers):
    return {"internalDate": str(when), "payload": {"mimeType": "text/plain", "headers": [{"name": "From", "value": sender}, *[{"name": k, "value": v} for k, v in headers.items()]], "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


class BriefingPolicyTest(unittest.TestCase):
    def test_newsletter_is_excluded_even_when_gmail_calls_it_an_update(self):
        self.assertFalse(mail_policy.personal_message({"from": "The Neuron <news@newsletter.example.org>", "labels": ["CATEGORY_UPDATES"]}))
        self.assertFalse(mail_policy.personal_message({"from": "Author <person@example.org>", "headers": {"List-Unsubscribe": "https://example.org/unsubscribe"}}))

    def test_eveline_is_excluded_in_both_directions(self):
        self.assertFalse(mail_policy.personal_message({"from": "Eveline <private@example.org>"}))
        self.assertTrue(mail_policy.excluded_person("eveline.person@example.org"))
        self.assertTrue(mail_policy.excluded_person("EVELINE <private@example.org>"))

    def test_real_business_contact_is_allowed(self):
        self.assertTrue(mail_policy.personal_message({"from": "Person <person@company.example>", "labels": ["CATEGORY_UPDATES"]}))

    def test_decline_is_closed_despite_a_question_in_quoted_history(self):
        body = "Hallo,\nIch habe mich f\u00fcr einen anderen Wagen entschieden.\nViele Gr\u00fc\u00dfe\nChristian\nVon: Sales <sales@example.org>\nBesteht noch Interesse?"
        self.assertTrue(mail_policy.closed_reply(body))
        self.assertFalse(mail_policy.requests_response("Re: Besteht noch Interesse?", body))
        self.assertNotIn("Besteht noch", mail_policy.authored_text(body))

    def test_confirmation_is_not_an_unanswered_request(self):
        self.assertFalse(daily_cody.looks_waiting_for_reply("Re: Termin?", "Mittwoch um 15 Uhr passt mir. Ich erg\u00e4nze die Unterlagen."))
        self.assertFalse(daily_cody.looks_waiting_for_reply("Link", "Hier ist der Link: https://example.org/?q=1"))
        self.assertTrue(daily_cody.looks_waiting_for_reply("Termin", "K\u00f6nnten Sie den Termin best\u00e4tigen?"))

    def test_latest_own_decline_closes_the_conversation(self):
        thread = {"messages": [mime_message("Sales <sales@example.org>", "Noch Interesse?", 1000), mime_message("Owner <owner@example.org>", "Ich habe mich f\u00fcr einen anderen Wagen entschieden.", 2000)]}
        with patch.object(daily_cody, "request_json", return_value=thread):
            state = daily_cody.conversation_state("token", "thread", {"owner@example.org"}, 1000)
        self.assertTrue(state["closed"])
        self.assertTrue(state["answered"])

    def test_answered_incoming_mail_is_not_another_reply_task(self):
        when = int((NOW - dt.timedelta(days=1)).timestamp() * 1000)
        incoming = {"from": "Person <person@example.org>", "subject": "Bitte best\u00e4tigen", "body": "K\u00f6nnten Sie best\u00e4tigen?", "answered": True, "sort_key": when}
        self.assertEqual(daily_cody.list_yesterday_open_mail("token", NOW, [incoming]), [])
        incoming["answered"] = False
        self.assertEqual(len(daily_cody.list_yesterday_open_mail("token", NOW, [incoming])), 1)

    def test_source_mode_does_not_call_ai_or_add_filler(self):
        with patch.object(daily_cody, "build_ai_briefing") as ai, patch.dict("os.environ", {"CODY_GENERATION_MODE": "source"}):
            result = daily_cody.build_briefing(SimpleNamespace(openai_api_key="unused"), NOW, {"summary": "DWD-Messwerte"}, [], [], [], [], None, {}, [], [], [], [])
        ai.assert_not_called()
        self.assertNotIn("sch\u00f6ner kleiner Bonus", result)

    def test_today_filler_is_removed_even_from_opt_in_ai_output(self):
        context = {"weather": {"summary": "Messwerte"}, "today_events": [], "world_cup_games": [], "deliveries": [], "today_todos": [], "yesterday_open_mail": [], "waiting_for": []}
        result = daily_cody.finalize_briefing("# Daily Cody\n## Today\n- Wetter\n- Offene F\u00e4den bestellen eigene M\u00f6bel.\n## Deliveries\n- Quatsch", context)
        self.assertNotIn("M\u00f6bel", result)

    def test_missing_monitor_snapshot_uses_explicit_live_source(self):
        incoming = {"from": "Person <person@example.org>", "subject": "Vertragsentwurf", "body": "Der Vertragsentwurf folgt diese Woche.", "answered": True, "source_url": "https://mail.google.com/mail/u/0/#all/example", "sort_key": int(NOW.timestamp() * 1000)}
        with patch.object(daily_cody.follow_up_snapshot, "read_snapshot", return_value=(None, "fehlt")):
            result = daily_cody.build_briefing(SimpleNamespace(openai_api_key=None), NOW, {"summary": "Messwerte"}, [], [], [], [], None, {}, [incoming], [], [], [])
        self.assertIn("Gmail live", result)
        self.assertIn("Der Vertragsentwurf folgt diese Woche", result)
        self.assertIn("Antwort bereits gesendet", result)

    def test_private_chat_subscription_and_car_sales_are_not_packages(self):
        for sender, subject, body in [
            ("Friend <friend@example.org>", "Wurst", "Heute habe ich Wurst bestellt."),
            ("Service <subscription@example.org>", "Ihr Abonnement", "Ihre Bestellung 123456 wird versendet."),
            ("Sales <sales@meinauto.example>", "Ihre Anfrage", "MeinAuto Leasing: Angebot 123456 wurde versendet."),
        ]:
            with self.subTest(subject=subject):
                self.assertEqual(delivery_detection.detect_open_deliveries([{"from": sender, "subject": subject, "body": body}], NOW), [])

    def test_bestsecret_search_keeps_trash_but_personal_queries_do_not(self):
        queries = delivery_detection.delivery_search_queries()
        merchant = [q for q in queries if "from:service.bestsecret.com" in q]
        self.assertTrue(merchant)
        self.assertTrue(all("-in:trash" not in q for q in merchant))

    def test_delivery_api_enables_trash_on_every_page(self):
        with patch.object(delivery_detection, "delivery_search_queries", return_value=["in:anywhere -in:spam from:service.bestsecret.com"]), patch.object(daily_cody, "request_json", side_effect=[{"messages": [{"id": "a"}], "nextPageToken": "second"}, {"messages": [{"id": "b"}]}]) as request:
            result = daily_cody.search_delivery_message_refs("token")
        self.assertEqual(len(result), 2)
        for call in request.call_args_list:
            params = urllib.parse.parse_qs(urllib.parse.urlparse(call.args[0]).query)
            self.assertEqual(params["includeSpamTrash"], ["true"])
            self.assertIn("-in:spam", params["q"][0])

    def test_completion_search_does_not_download_generated_briefings(self):
        with patch.object(daily_cody, "search_gmail_refs", return_value=[]) as search, patch.object(daily_cody, "request_json") as fetch:
            self.assertEqual(daily_cody.list_completed_delivery_mail_topics("token", "Owner <owner+cody@example.org>", "owner@example.org"), [])
        self.assertIn('-subject:"The Daily Cody"', search.call_args.args[1])
        fetch.assert_not_called()

    def test_bestsecret_html_is_used_when_plain_part_is_only_a_footer(self):
        html = '<head><style>' + "css " * 4000 + '</style></head><div style="display:none">' + "padding " * 1000 + '</div><p>Wir bereiten Ihre Bestellung vor.</p><p>Bestellnummer: 1234567890</p>'
        payload = {"parts": [mime_message("", "Footer und App-Download", 0)["payload"], {"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()}}]}
        body = daily_cody.extract_message_text(payload, prefer_html=True)
        result = delivery_detection.detect_open_deliveries([{"from": "BESTSECRET <noreply@service.bestsecret.com>", "subject": "Vielen Dank f\u00fcr Ihre Bestellung", "body": body, "labels": ["TRASH"], "sort_key": int(NOW.timestamp() * 1000)}], NOW)
        self.assertEqual(result[0]["subject"], "BestSecret #1234567890")
        self.assertEqual(result[0]["status"], "ordered")
        self.assertNotIn("css", body)

    def test_visible_html_removes_quoted_threads(self):
        self.assertEqual(mail_text.visible_html('<p>Termin passt.</p><div class="gmail_quote">Noch Interesse?</div>'), "Termin passt.")

    def test_search_follows_pages_and_deduplicates(self):
        with patch.object(daily_cody, "request_json", side_effect=[{"messages": [{"id": "a"}], "nextPageToken": "page2"}, {"messages": [{"id": "a"}, {"id": "b"}]}]):
            result = daily_cody.search_gmail_refs("token", "query")
        self.assertEqual([item["id"] for item in result], ["a", "b"])

    def test_repeated_page_token_and_limits_fail_explicitly(self):
        with patch.object(daily_cody, "request_json", return_value={"nextPageToken": "same"}):
            with self.assertRaises(RuntimeError):
                daily_cody.search_gmail_refs("token", "query")
        with patch.object(daily_cody, "request_json", return_value={"messages": [{"id": "a"}, {"id": "b"}]}):
            with self.assertRaises(RuntimeError):
                daily_cody.search_gmail_refs("token", "query", maximum=1)

    def test_all_due_reminders_are_rendered_without_silent_limit(self):
        items = [{"title": f"Aufgabe {number}"} for number in range(15)]
        self.assertEqual(len(daily_cody.format_today_todo_reminders(items)), 15)

    def test_reply_task_requires_no_generated_suggested_answer(self):
        lines = daily_cody.format_open_mail_items([{"subject": "Rueckfrage", "from": "Person <person@example.org>", "source_url": "https://mail.google.com/mail/u/0/#all/example"}])
        self.assertIn("Antwort offen", lines[0])
        self.assertIn("[Mail]", lines[0])

    def test_resolved_meinauto_does_not_enter_live_follow_up(self):
        incoming = {"from": "Sales <sales@meinauto.de>", "subject": "MeinAuto Angebot", "body": "Ist das Angebot noch interessant?", "sort_key": int(NOW.timestamp() * 1000)}
        with patch.object(daily_cody.follow_up_snapshot, "read_snapshot", return_value=(None, "fehlt")):
            result = daily_cody.build_briefing(SimpleNamespace(openai_api_key=None), NOW, {"summary": "Messwerte"}, [], [], [], [], None, {}, [incoming], [], [incoming], [incoming])
        self.assertNotIn("MeinAuto", result)

    def test_order_confirmation_does_not_survive_its_exact_tracking_delivery(self):
        messages = [
            {"from": "BESTSECRET <noreply@service.bestsecret.com>", "subject": "Vielen Dank fuer Ihre Bestellung", "body": "Bestellnummer: 1234567890", "sort_key": 1000},
            {"from": "BESTSECRET <noreply@service.bestsecret.com>", "subject": "Ihre Bestellung ist versandbereit", "body": "Bestellnummer: 1234567890 Sendungsnummer: 00340434515530000000", "sort_key": 2000},
            {"from": "DHL <noreply@dhl.de>", "subject": "Ihre BESTSECRET Sendung wurde zugestellt", "body": "Sendungsnummer: 00340434515530000000", "sort_key": 3000},
        ]
        self.assertEqual(delivery_detection.detect_open_deliveries(messages, NOW), [])
        messages.append({"from": "BESTSECRET <noreply@service.bestsecret.com>", "subject": "Ihre Bestellung ist versandbereit", "body": "Bestellnummer: 1234567890 Sendungsnummer: 00340434515530000001", "sort_key": 2000})
        result = delivery_detection.detect_open_deliveries(messages, NOW)
        self.assertEqual(len(result), 1)
        self.assertIn("1234567890", result[0]["subject"])

    def test_old_delivery_does_not_close_new_order_from_same_merchant(self):
        messages = [
            {"from": "BESTSECRET <noreply@service.bestsecret.com>", "subject": "Vielen Dank f\u00fcr Ihre Bestellung", "body": "Bestellnummer: 1234567890", "sort_key": int(NOW.timestamp() * 1000)},
            {"from": "DHL <noreply@dhl.de>", "subject": "Ihre BESTSECRET Sendung wurde zugestellt", "body": "Sendungsnummer: 00340434515530000000", "sort_key": 1000},
        ]
        result = delivery_detection.detect_open_deliveries(messages, NOW)
        self.assertEqual(result[0]["subject"], "BestSecret #1234567890")

    def test_more_than_eight_deliveries_are_not_silently_dropped(self):
        messages = [{"from": "BESTSECRET <noreply@service.bestsecret.com>", "subject": "Vielen Dank f\u00fcr Ihre Bestellung", "body": f"Bestellnummer: {1234567800 + number}", "sort_key": int(NOW.timestamp() * 1000)} for number in range(10)]
        self.assertEqual(len(delivery_detection.detect_open_deliveries(messages, NOW)), 10)

    def test_tomorrow_eta_is_relative_to_mail_date_not_run_date(self):
        mail_at = NOW - dt.timedelta(days=2)
        result = delivery_detection.detect_open_deliveries([{"from": "Amazon <shipment@amazon.de>", "subject": "Versendet: Ihre Bestellung", "body": "Bestellnummer: 305-1234567-1234567 Ankunft morgen", "sort_key": int(mail_at.timestamp() * 1000)}], NOW)
        self.assertEqual(result[0]["status"], "unconfirmed")
        self.assertEqual(result[0]["eta_end_date"], (mail_at.date() + dt.timedelta(days=1)).isoformat())


if __name__ == "__main__":
    unittest.main()
