import datetime as dt
import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import commerce_reconciliation as c
import daily_cody
import delivery_detection as d
import mail_summary

NOW = dt.datetime(2026, 10, 12, 8, tzinfo=ZoneInfo("Europe/Berlin"))
T = int(NOW.timestamp() * 1000)


def mail(body, subject="Bestellung 305-1234567-1234567", **kwargs):
    return {"from": "Amazon <order-update@amazon.de>", "subject": subject, "body": body,
            "message_id": "fixture-1", "thread_id": "fixture-thread-1", "sort_key": T - 1000, **kwargs}


class CommerceTest(unittest.TestCase):
    def test_delivery_announcement_negation_and_dropoff_preference_are_not_delivery(self):
        for body in ("Wird heute zugestellt", "Wird morgen geliefert", "Wurde nicht zugestellt",
                     "Bitte wählen Sie einen Ablageort", "Noch nicht geliefert"):
            with self.subTest(body=body):
                self.assertNotEqual(d.classify_delivery_status("Sendungsstatus", "", body), "delivered")

    def test_amazon_delivery_closes_matching_order_across_threads_only(self):
        shipped = mail("Bestellnummer 305-1234567-1234567", "Versendet: Artikel")
        delivered = mail("Bestellnummer 305-1234567-1234567", "Geliefert: Artikel", thread_id="other", sort_key=T)
        other = mail("Bestellnummer 305-1234567-7654321", "Versendet: gleicher Artikel", message_id="other", thread_id="third")
        result = d.detect_open_deliveries([shipped, delivered, other], NOW)
        self.assertEqual(len(result), 1)
        self.assertIn("7654321", result[0]["subject"])

    def test_carrier_closes_same_tracking_but_not_other_parcel(self):
        a = mail("Sendungsnummer 123456789012", "Paket wurde versandt")
        b = mail("Sendungsnummer 123456789012", "Ihr Paket wurde zugestellt", **{"from": "DHL <zustellung@dhl.de>", "sort_key": T, "thread_id": "carrier"})
        other = mail("Sendungsnummer 999999999999", "Versendet: anderes Paket", message_id="other")
        self.assertEqual(len(d.detect_open_deliveries([a, b, other], NOW)), 1)

    def test_financial_announcements_and_invoice_credit_are_not_completed_payments(self):
        for body in ("Die Rückerstattung wurde veranlasst.", "Der Betrag wird erstattet.",
                     "Der Betrag wurde noch nicht erstattet.", "Der Rechnungsbetrag wurde verringert.",
                     "Der Betrag wurde teilweise erstattet.", "Die Rückerstattung ist abgeschlossen.",
                     "Die Bezahlung ist geplant.", "Die Zahlung ist teilweise eingegangen."):
            with self.subTest(body=body):
                self.assertEqual(c.completion_states(mail(body)), set())

    def test_payment_and_refund_are_separate_and_match_across_threads(self):
        pending = mail("Bitte Erstattung prüfen.")
        paid = mail("Zahlung eingegangen.", thread_id="payment", sort_key=T)
        self.assertFalse(c.is_completed(pending, c.completion_events([paid])))
        refunded = mail("Der Betrag wurde vollständig erstattet.", thread_id="refund", sort_key=T)
        events = c.completion_events([refunded])
        self.assertTrue(c.is_completed(pending, events))
        self.assertFalse(c.is_completed(mail("Bitte Erstattung prüfen.", subject="Bestellung 305-0000000-1234567", message_id="different", thread_id="different"), events))

    def test_same_numeric_order_at_other_merchant_does_not_close(self):
        pending = mail("Rückzahlung prüfen", "Bestellnummer 987654", **{"from": "Shop One <service@one.example>"})
        done = mail("Bestellnummer 987654 Zahlung eingegangen", **{"subject": "Bestellbestätigung Lieferung", "from": "Shop Two <service@two.example>", "sort_key": T, "message_id": "other", "thread_id": "other"})
        self.assertFalse(c.is_completed(pending, c.completion_events([done])))

    def test_user_confirmation_closes_exact_case_but_keeps_future_dispute(self):
        pending = mail("Rückerstattung prüfen")
        event = {"identities": list(c.identities(pending)), "states": ["refunded"], "through_ms": T, "source": "user_confirmation"}
        self.assertTrue(c.is_completed(pending, [event]))
        self.assertFalse(c.is_completed({**pending, "sort_key": T + 1}, [event]))

    def test_message_confirmation_does_not_hide_future_replacement_shipment(self):
        old = mail("Bestellnummer 305-1234567-1234567", "Versendet: Artikel")
        new = {**old, "message_id": "replacement", "sort_key": T, "body": old["body"] + " Ersatzsendung"}
        completed = [d.delivery_completion_fingerprint("message", old["message_id"])]
        result = d.detect_open_deliveries([old, new], NOW, completed_topics=completed)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["message_id"], "replacement")

    def test_user_confirmation_matches_monitor_source_id_without_merchant_wildcard(self):
        original = mail("Rückgabe", subject="Rückgabe", **{"from": "Shop <service@shop.example>"})
        event = {"identities": list(c.identities(original)), "states": ["refunded"], "through_ms": T}
        point = {"topic": "Rückgabe", "sources": [{"kind": "gmail", "id": "fixture-1"}]}
        self.assertTrue(c.is_completed(point, [event]))
        self.assertFalse(c.is_completed({**point, "sources": [{"kind": "gmail", "id": "new"}]}, [event]))
        self.assertFalse(c.is_completed({**point, "observed_at": (NOW + dt.timedelta(hours=1)).isoformat()}, [event]))

    def test_long_term_and_travel_cases_are_not_closed(self):
        for topic in ("Tesla Bestellung", "Experteer Rechnung", "BA Reisekosten", "BSI Reisekosten"):
            item = mail("Zahlung prüfen", subject=topic)
            event = {"identities": list(c.identities(item)), "states": ["delivered", "paid", "refunded"], "through_ms": T}
            self.assertFalse(c.is_completed(item, [event]))

    def test_manual_confirmation_filters_mail_and_reminder_during_build(self):
        pending = mail("Bitte Rückerstattung prüfen.", **{"from": "Person <person@example.org>"})
        event = {"identities": list(c.identities(pending)), "states": ["refunded"], "through_ms": T}
        with patch.object(c, "load_confirmations", return_value=[event]):
            reminder = {"title": "Rückerstattung nachfragen", "message_id": "fixture-1", "due": "2026-10-11"}
            result = daily_cody.build_briefing(SimpleNamespace(openai_api_key=None), NOW, {"summary": "Wetter"}, [], [], [], [reminder], None, {}, [pending], [], [pending], [])
        self.assertNotIn("Erstattung offen", result)
        self.assertNotIn("Antwort offen", result)
        self.assertNotIn("Rückerstattung nachfragen", result)


class MailSummaryTest(unittest.TestCase):
    def test_long_body_is_paraphrased_and_deadline_survives(self):
        item = mail("Hintergrunddetails. " * 100 + "Bitte antworten Sie bis zum 15.10.2026 um 12:00 Uhr.\nViele Grüße\nSignatur\nAm Dienstag schrieb jemand: alte Frage?", "Rückfrage", **{"from": "Person <person@example.org>"})
        line = mail_summary.format_item(item)
        self.assertLess(len(line), 350)
        self.assertIn("15.10.2026 um 12:00 Uhr", line)
        self.assertIn("Person", line)
        self.assertIn("[Mail]", line)
        self.assertNotIn("Hintergrunddetails", line)
        self.assertNotIn("Signatur", line)
        self.assertNotIn("alte Frage", line)

    def test_late_negation_is_not_cut_into_positive_statement(self):
        line = mail_summary.format_item(mail("Einleitung. " * 80 + "Der Vertragsentwurf folgt nicht. Keine Antwort bis zum 15.10.2026 nötig."))
        self.assertIn("Einschränkung", line)
        self.assertNotIn("angekündigt", line)
        self.assertNotIn("Frist:", line)

    def test_waiting_and_answered_mail_never_quote_body(self):
        item = mail("Hintergrunddetails. " * 100, **{"to": "Support <support@example.org>"})
        for flags in ({"waiting": True}, {"answered": True}):
            with self.subTest(flags=flags):
                line = mail_summary.format_item(item, **flags)
                self.assertNotIn("Hintergrunddetails", line)
                self.assertIn("[Mail]", line)

    def test_relative_deadline_is_visible_without_inventing_a_date(self):
        line = mail_summary.format_item(mail("Bitte antworten Sie spätestens bis Freitag."))
        self.assertIn("Frist: Angabe in der Mail prüfen", line)

    def test_realistic_bestsecret_and_riverty_wording_keeps_uncertainty(self):
        self.assertIn("Auszahlung", mail_summary.core("Retoure bearbeitet", "Der Rechnungsbetrag wurde verringert."))
        self.assertIn("noch kein Nachweis", mail_summary.core("Bezahlung geplant", "Du bezahlst per Lastschrift. Der Betrag wird demnächst abgebucht."))

    def test_payment_faq_does_not_create_reply_task(self):
        item = mail("Du bezahlst per Lastschrift. Der Betrag wird demnächst abgebucht. Hast Du Fragen zur Zahlung?", **{"from": "Riverty <noreply-de@info.riverty.com>"})
        self.assertNotIn("Antwort offen", mail_summary.format_item(item))


class DryRunTest(unittest.TestCase):
    def test_allow_duplicate_dry_run_reads_sources_but_never_sends(self):
        config = SimpleNamespace(timezone="Europe/Berlin", force_send=True,
                                 allow_duplicate=True, dry_run=True,
                                 sender="sender@example.org", recipient="recipient@example.org")
        results = {
            "load_config": config, "refresh_google_token": "fake-token",
            "get_weather": {"summary": "Test"}, "list_calendar_events": ([], []),
            "list_world_cup_games": [], "read_exported_reminders": ([], None),
            "read_application_wiki_snapshot": {}, "list_recent_mail": [],
            "list_delivery_mail": [], "list_yesterday_open_mail": [],
            "list_waiting_for_mail": [], "build_briefing": "Test briefing",
        }
        with contextlib.ExitStack() as stack:
            mocks = {name: stack.enter_context(patch.object(daily_cody, name, return_value=value))
                     for name, value in results.items()}
            render = stack.enter_context(patch.object(daily_cody, "markdown_to_basic_html", wraps=daily_cody.markdown_to_basic_html))
            send = stack.enter_context(patch.object(daily_cody, "send_email"))
            stack.enter_context(patch.object(sys, "argv", ["daily_cody.py"]))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(daily_cody.main(), 0)
            mocks["list_delivery_mail"].assert_called_once()
            mocks["build_briefing"].assert_called_once()
            send.assert_not_called()
            render.assert_called_once_with("Test briefing", {"summary": "Test"})
            self.assertIn("Rendered output verified", output.getvalue())
            self.assertIn("Dry run successful", output.getvalue())
            self.assertIn("no email sent", output.getvalue())


if __name__ == "__main__":
    unittest.main()
