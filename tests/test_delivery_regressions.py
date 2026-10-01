"""Anonymized regressions for terminal events, source dates and tracking URLs."""

import datetime as dt
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import quote
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody
import delivery_detection as deliveries


ZONE = ZoneInfo("Europe/Berlin")
NOW = dt.datetime(2026, 10, 1, 6, tzinfo=ZONE)
ORDER = "999-1111111-2222222"
OTHER_ORDER = "999-1111111-3333333"


def message(subject, body, *, order=ORDER, hours_ago=12, **extra):
    return {
        "from": "Shop <order-update@amazon.de>",
        "subject": subject,
        "body": f"{body}\nBestellnummer: {order}" if order else body,
        "sort_key": int((NOW - dt.timedelta(hours=hours_ago)).timestamp() * 1000),
        "message_id": "example-message",
        **extra,
    }


class CancellationRegressionTest(unittest.TestCase):
    def test_confirmed_cancellation_closes_exact_order_not_other_order(self):
        old = message("Bestellt: Kabel", "Bestellung bestätigt. Ankunft morgen")
        cancel = message("Artikel erfolgreich storniert: Kabel", "Deine Artikel wurden storniert.\nDeine Bestellung wurde storniert. Diese Bestellung wurde dir nicht in Rechnung gestellt.", hours_ago=10)
        other = message("Bestellt: Adapter", "Bestellung bestätigt.", order=OTHER_ORDER)
        self.assertEqual(deliveries.classify_delivery_status(cancel["subject"], "", cancel["body"]), "cancelled")
        output = deliveries.detect_open_deliveries([old, other, cancel], NOW)
        self.assertEqual([item["subject"] for item in output], [f"Amazon #{OTHER_ORDER}"])

    def test_whole_order_cancellation_closes_older_tracking_groups(self):
        first = message("Versendet: Kabel", "Sendungsnummer: 00340434515530000111")
        second = message("Versendet: Adapter", "Sendungsnummer: 00340434515530000222")
        cancel = message("Stornierung bestätigt", "Ihre Bestellung wurde erfolgreich storniert.", hours_ago=8)
        self.assertEqual(deliveries.detect_open_deliveries([first, second, cancel], NOW), [])

    def test_later_source_update_can_reopen_same_order(self):
        cancel = message("Stornierung", "Ihre Bestellung wurde storniert.")
        later = message("Versendet: Kabel", "Dein Paket wurde versendet.", hours_ago=3)
        self.assertEqual(len(deliveries.detect_open_deliveries([cancel, later], NOW)), 1)

    def test_partial_cancellation_does_not_close_other_items(self):
        for body in (
            "Ein Artikel wurde storniert.",
            "Ein Teil Ihrer Bestellung wurde storniert.",
            "One item in your order was cancelled.",
            "Ein Teil Ihrer\nBestellung wurde storniert.",
            "One item in your\norder was cancelled.",
            "An item from your order was cancelled.",
            "Ein Artikel aus Ihrer aktuellen Bestellung wurde storniert.",
            "Ein Teil der folgenden Bestellung wurde storniert.",
            "Ein Artikel aus Ihrer Amazon-Bestellung wurde storniert.",
            "Ein Produkt aus Ihrer Bestellung wurde storniert.",
            "One product in your order was cancelled.",
        ):
            with self.subTest(body=body):
                self.assertFalse(deliveries.confirmed_order_cancellation(body))
                old = message("Bestellt: Kabel und Adapter", "Bestellung bestätigt.")
                cancel = message("Artikel erfolgreich storniert", body, hours_ago=8)
                self.assertEqual(len(deliveries.detect_open_deliveries([old, cancel], NOW)), 1)

    def test_requests_negation_questions_and_advice_are_not_terminal(self):
        for body in (
            "Bitte stornieren Sie meine Bestellung.",
            "Ihre Bestellung wurde nicht storniert.",
            "Die Stornierung Ihrer Bestellung ist fehlgeschlagen.",
            "Deine Bestellung wurde storniert?",
            "If your order was cancelled, contact support.",
            "Wenn du möchtest, kannst du die Bestellung stornieren.",
            "Prüfe bitte, ob deine Bestellung wurde storniert.",
        ):
            with self.subTest(body=body):
                self.assertFalse(deliveries.confirmed_order_cancellation(body))

    def test_english_confirmations_are_supported(self):
        for body in ("Your order has been cancelled.", "Your order was canceled."):
            self.assertTrue(deliveries.confirmed_order_cancellation(body))
            old = message("Bestellt: Kabel", "Bestellung bestätigt.")
            cancellation = message("Cancellation confirmed", body, hours_ago=8)
            self.assertEqual(deliveries.detect_open_deliveries([old, cancellation], NOW), [])

    def test_wrapped_whole_order_notice_after_item_heading_closes_order(self):
        body = "Deine Artikel wurden storniert\nStorniert\nDeine Bestellung wurde\nstorniert. Diese Bestellung wurde nicht berechnet."
        old = message("Bestellt: Kabel", "Bestellung bestätigt.")
        cancellation = message("Artikel erfolgreich storniert", body, hours_ago=8)
        self.assertEqual(deliveries.detect_open_deliveries([old, cancellation], NOW), [])

    def test_cancellation_needs_order_identity_and_known_timestamp(self):
        old = message("Bestellt: Kabel", "Bestellung bestätigt.")
        cancel = message("Stornierung", "Deine Bestellung wurde storniert.", order="", hours_ago=8)
        self.assertEqual(len(deliveries.detect_open_deliveries([old, cancel], NOW)), 1)
        cancel = message("Stornierung", "Deine Bestellung wurde storniert.", sort_key=0)
        self.assertEqual(len(deliveries.detect_open_deliveries([old, cancel], NOW)), 1)

    def test_terminal_status_never_reaches_rendered_deliveries(self):
        items = [{"subject": "Order", "status": "cancelled", "snippet": "storniert"}]
        self.assertEqual(daily_cody.format_delivery_items(items), ["- Keine offenen Liefer- oder Bestellmails gefunden."])


class RelativeDateRegressionTest(unittest.TestCase):
    def test_quoted_relative_date_does_not_override_current_eta(self):
        source = message("Ihre Sendung kommt heute", "Zustellung: heute\nAm Mittwoch schrieb Service:\nAnkunft morgen", hours_ago=1)
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["eta_end_date"], "2026-10-01")
        self.assertEqual(result[0]["snippet"], "in Zustellung, voraussichtliche Zustellung 01.10.2026")

    def test_yesterday_tomorrow_is_absolute_in_plain_and_html_output(self):
        source = message("Versendet: Augenmaske", "Dein Paket wurde versendet. Ankunft morgen", hours_ago=7)
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["eta_end_date"], "2026-10-01")
        self.assertEqual(result[0]["snippet"], "versendet, voraussichtliche Zustellung 01.10.2026")
        plain = "## Deliveries\n" + "\n".join(daily_cody.format_delivery_items(result))
        html = daily_cody.markdown_to_basic_html(plain)
        for output in (plain, html):
            self.assertIn("01.10.2026", output)
            self.assertNotIn("morgen", output)

    def test_rfc_email_date_without_internal_timestamp_is_anchored(self):
        source = message("Versendet: Augenmaske", "Ankunft morgen", sort_key=0, date="Wed, 30 Sep 2026 21:00:00 +0000")
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["eta_end_date"], "2026-10-01")
        self.assertIn("01.10.2026", result[0]["snippet"])

    def test_utc_previous_day_after_berlin_midnight_uses_local_date(self):
        when = dt.datetime(2026, 9, 30, 22, 30, tzinfo=dt.timezone.utc)
        source = message("Versendet: Augenmaske", "Zustellung: morgen", sort_key=int(when.timestamp() * 1000))
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["eta_end_date"], "2026-10-02")
        self.assertIn("02.10.2026", result[0]["snippet"])

    def test_today_is_dated_and_past_eta_remains_unconfirmed(self):
        source = message("Ihre Sendung kommt heute", "Wird heute zugestellt.", hours_ago=24)
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["status"], "unconfirmed")
        self.assertNotIn("heute", result[0]["snippet"])

    def test_explicit_ranges_are_not_collapsed_to_a_single_date(self):
        source = message("Versendet: Buch", "Zustellung: 1. Oktober–3. Oktober", hours_ago=7)
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["eta_end_date"], "2026-10-03")
        self.assertIn("1. Oktober–3. Oktober", result[0]["snippet"])


class TrackingLinkRegressionTest(unittest.TestCase):
    def test_navigation_path_ref_suffix_is_not_tracking(self):
        for path in ("/gp/css/order-history/ref=nav_orders", "/gp/your-account/ref=nav_account"):
            direct = f"https://www.amazon.de{path}"
            wrapped = "https://www.amazon.de/gp/r.html?U=" + quote(direct, safe="")
            for link in (direct, wrapped):
                with self.subTest(link=link):
                    self.assertEqual(deliveries.extract_tracking_links(f"Meine Bestellungen {link}"), [])

    def test_package_trackers_under_account_paths_are_retained(self):
        for path in ("/gp/your-account/ship-track", "/gp/css/shiptrack/view.html"):
            tracker = f"https://www.amazon.de{path}?orderId=example"
            wrapped = "https://www.amazon.de/gp/r.html?U=" + quote(tracker, safe="")
            for link in (tracker, wrapped):
                with self.subTest(link=link):
                    self.assertEqual(deliveries.extract_tracking_links(f"https://www.dhl.de/\nLieferung verfolgen {link}")[0], link)

    def test_malformed_url_does_not_break_delivery_parsing(self):
        self.assertEqual(deliveries.extract_tracking_links("https://[invalid/tracking"), [])

    def test_actual_tracker_precedes_navigation_and_carrier_home(self):
        tracker = "https://www.amazon.de/progress-tracker/package?orderId=example"
        body = f"Meine Bestellungen https://www.amazon.de/gp/css/order-history?ref_=example\nhttps://www.dhl.de/\nLieferung verfolgen {tracker}"
        self.assertEqual(deliveries.extract_tracking_links(body)[0], tracker)
        self.assertNotIn("order-history", " ".join(deliveries.extract_tracking_links(body)))

    def test_amazon_redirect_targets_are_ranked_not_their_wrapper(self):
        history = "https://www.amazon.de/gp/r.html?U=" + quote("https://www.amazon.de/gp/css/order-history", safe="")
        tracker = "https://www.amazon.de/gp/r.html?U=" + quote("https://www.amazon.de/progress-tracker/package?orderId=example", safe="")
        self.assertEqual(deliveries.extract_tracking_links(f"{history}\nLieferung verfolgen {tracker}"), [tracker])

    def test_navigation_only_falls_back_to_source_mail(self):
        source = message("Versendet: Augenmaske", "Ankunft morgen https://www.amazon.de/gp/css/order-history", hours_ago=7)
        result = deliveries.detect_open_deliveries([source], NOW)
        self.assertEqual(result[0]["tracking_links"], [])
        line = daily_cody.format_delivery_items(result)[0]
        self.assertIn("[Bestellmail]", line)
        self.assertNotIn("[Trackinglink]", line)


class BriefingOutputRegressionTest(unittest.TestCase):
    def test_final_plain_and_html_exclude_cancelled_and_automated_tasks(self):
        old = message("Bestellt: Kabel", "Bestellung bestätigt.")
        cancel = message("Stornierung bestätigt", "Deine Bestellung wurde storniert.", hours_ago=10)
        notice = message(
            "Versendet: Augenmaske", "Dein Paket wurde versendet. Ankunft morgen.\n"
            "Wenn du möchtest, kannst du den Artikel zurücksenden.",
            order=OTHER_ORDER, hours_ago=7,
            **{"from": "Shop <versandbestaetigung@amazon.de>", "labels": ["CATEGORY_UPDATES"]},
        )
        current = deliveries.detect_open_deliveries([old, cancel, notice], NOW)
        with patch.object(daily_cody.follow_up_snapshot, "read_snapshot", return_value=(None, "fehlt")), patch.object(
            daily_cody, "load_resolved_topic_overrides", return_value=[],
        ), patch.object(daily_cody, "daily_morning_quote", return_value="Testzitat"), patch.dict(
            "os.environ", {"CODY_GENERATION_MODE": "source"},
        ), patch.object(daily_cody, "send_email") as send:
            plain = daily_cody.build_briefing(
                SimpleNamespace(openai_api_key=None), NOW, {"summary": "Testwetter"},
                [], [], [], [], None, {}, [notice], current, [notice], [],
            )
            html = daily_cody.markdown_to_basic_html(plain)
        send.assert_not_called()
        for rendered in (plain, html):
            self.assertNotIn(ORDER, rendered)
            self.assertIn(OTHER_ORDER, rendered)
            self.assertIn("voraussichtliche Zustellung 01.10.2026", rendered)
            self.assertNotIn("Ankunft morgen", rendered)
            self.assertNotIn("Antwort offen:", rendered)
            self.assertNotIn("Versendet: Augenmaske", rendered)


if __name__ == "__main__":
    unittest.main()
