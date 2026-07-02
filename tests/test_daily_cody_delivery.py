import datetime as dt
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import delivery_detection  # noqa: E402


NOW = dt.datetime(2026, 6, 27, 8, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def internal_date(hours_ago: int) -> int:
    return int((NOW - dt.timedelta(hours=hours_ago)).timestamp() * 1000)


def delivery_item(**overrides):
    item = {
        "from": "Amazon.de <shipment-tracking@amazon.de>",
        "subject": "Amazon #305-1314679-9745914",
        "date": "Sat, 27 Jun 2026 07:00:00 +0200",
        "snippet": "versendet",
        "status": "shipped",
        "status_rank": delivery_detection.delivery_status_rank("shipped"),
        "topic_key": "amazon.de:order:305-1314679-9745914",
        "thread_id": "thread-1",
        "sort_key": internal_date(4),
        "eta_end_date": "",
        "tracking_links": ["https://www.amazon.de/progress-tracker/package"],
        "details": "",
    }
    item.update(overrides)
    return item


class DeliveryFilteringTest(unittest.TestCase):
    def test_delivered_updates_are_never_returned(self):
        items = [
            delivery_item(
                snippet="geliefert",
                status="delivered",
                status_rank=delivery_detection.delivery_status_rank("delivered"),
            )
        ]

        self.assertEqual(delivery_detection.summarize_delivery_candidates(items, NOW), [])

    def test_delivered_update_suppresses_older_shipping_mail(self):
        items = [
            delivery_item(snippet="versendet", sort_key=internal_date(24)),
            delivery_item(
                snippet="geliefert",
                status="delivered",
                status_rank=delivery_detection.delivery_status_rank("delivered"),
                sort_key=internal_date(2),
            ),
        ]

        self.assertEqual(delivery_detection.summarize_delivery_candidates(items, NOW), [])

    def test_shipping_mail_with_past_eta_is_stale(self):
        items = [delivery_item(snippet="versendet, Zustellung 18. Juni-19. Juni", eta_end_date="2026-06-19")]

        self.assertEqual(delivery_detection.summarize_delivery_candidates(items, NOW), [])
        self.assertEqual(
            delivery_detection.extract_delivery_eta_end_date(NOW, "versendet, Zustellung 18. Juni-19. Juni"),
            "2026-06-19",
        )

    def test_shipping_mail_with_future_eta_stays_visible(self):
        items = [delivery_item(snippet="versendet, Zustellung 28. Juni-29. Juni", eta_end_date="2026-06-29")]

        result = delivery_detection.summarize_delivery_candidates(items, NOW)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subject"], "Amazon #305-1314679-9745914")

    def test_bundesagentur_notifications_are_not_deliveries(self):
        self.assertTrue(
            delivery_detection.is_delivery_noise(
                "neue mitteilungen ihrer bundesagentur für arbeit",
                "",
                "In Ihrem Postfach wurde ein neues Dokument zugestellt.",
            )
        )

    def test_manual_cody_completion_topics_are_extracted(self):
        topics = delivery_detection.extract_completed_delivery_topics_from_text(
            "Cody Lieferung erhalten: Amazon #305-1314679-9745914\n"
            "Cody, Fix Foxi Album ist angekommen"
        )

        self.assertIn("Amazon #305-1314679-9745914", topics)
        self.assertIn("Fix Foxi Album", topics)
        self.assertTrue(
            delivery_detection.completed_delivery_topic_matches(
                "Amazon #305-1314679-9745914",
                "Amazon #305-1314679-9745914 versendet",
                delivery_detection.normalize_status_text("Amazon #305-1314679-9745914 versendet"),
            )
        )

    def test_completion_addresses_exclude_plain_recipient(self):
        addresses = delivery_detection.delivery_completion_request_addresses(
            "Cody Chief of Staff <christian.galler+cody@gmail.com>",
            "christian.galler@gmail.com",
        )

        self.assertIn("christian.galler+cody@gmail.com", addresses)
        self.assertNotIn("christian.galler@gmail.com", addresses)

    def test_future_dhl_zustellung_is_not_manual_completion(self):
        text = (
            "Ihre BESTSECRET Sendung wurde von uns bearbeitet und wird Ihnen voraussichtlich "
            "am Freitag, den 03.07. zugestellt. Ihr dauerhaft gebuchter Ablageort wird bei "
            "der Zustellung berücksichtigt."
        )

        self.assertEqual(delivery_detection.extract_completed_delivery_topics_from_text(text), [])

    def test_generic_completed_topics_do_not_suppress_active_deliveries(self):
        messages = [
            {
                "from": '"Amazon.de" versandbestaetigung@amazon.de',
                "subject": "Versendet: „Fliegengitter Balkontür...“",
                "snippet": "Versendet: „Fliegengitter Balkontür...“",
                "body": "",
                "thread_id": "amazon-thread",
                "sort_key": internal_date(1),
            },
            {
                "from": "DHL Paket <noreply@dhl.de>",
                "subject": "Ihre BESTSECRET Sendung ist unterwegs",
                "snippet": "Ihre Sendung ist unterwegs.",
                "body": "",
                "thread_id": "dhl-thread",
                "sort_key": internal_date(2),
            },
        ]

        result = delivery_detection.detect_open_deliveries(
            messages,
            NOW,
            completed_topics=["sendung", "paket", "bestellung", "bestsecret", "amazon"],
        )

        subjects = {item["subject"] for item in result}
        self.assertIn("Amazon-Bestellung", subjects)
        self.assertIn("BestSecret Sendung", subjects)

    def test_specific_completed_topic_still_suppresses_matching_delivery(self):
        self.assertTrue(
            delivery_detection.completed_delivery_topic_matches(
                "Fliegengitter Balkontür",
                "Versendet: Fliegengitter Balkontür mit Magnetverschluss",
                delivery_detection.normalize_status_text(
                    "Versendet: Fliegengitter Balkontür mit Magnetverschluss"
                ),
            )
        )
        self.assertTrue(
            delivery_detection.completed_delivery_topic_matches(
                "Amazon #305-1314679-9745914",
                "Amazon #305-1314679-9745914 versendet",
                delivery_detection.normalize_status_text("Amazon #305-1314679-9745914 versendet"),
            )
        )

    def test_delivery_search_queries_cover_known_merchants_and_carriers(self):
        queries = delivery_detection.delivery_search_queries()
        query_text = " ".join(queries).lower()

        for marker in ("amazon", "bestsecret", "golighter", "wellster", "dhl", "hermes"):
            self.assertIn(marker, query_text)
        for query in queries:
            self.assertEqual(query.count("{"), query.count("}"), query)
        self.assertIn("from:dhl.de", queries[0].lower())
        self.assertIn("from:amazon.de", queries[1].lower())
        simple_queries = [query for query in queries if "{" not in query and "}" not in query]
        for marker in ("from:dhl.de", "from:amazon.de", "from:service.bestsecret.com", "bestsecret"):
            self.assertTrue(any(marker in query.lower() for query in simple_queries), marker)

    def test_own_delivery_sender_matches_direct_and_cody_aliases(self):
        self.assertTrue(
            delivery_detection.is_own_delivery_sender(
                "Christian Galler <christian.galler@gmail.com>",
                "christian.galler+cody@gmail.com",
                "christian.galler@gmail.com",
            )
        )
        self.assertTrue(
            delivery_detection.is_own_delivery_sender(
                "Cody Chief of Staff <christian.galler+cody@gmail.com>",
                "christian.galler+cody@gmail.com",
                "christian.galler@gmail.com",
            )
        )
        self.assertFalse(
            delivery_detection.is_own_delivery_sender(
                "Amazon.de <shipment-tracking@amazon.de>",
                "christian.galler+cody@gmail.com",
                "christian.galler@gmail.com",
            )
        )

    def test_bestsecret_order_confirmation_is_detected(self):
        subject = "Vielen Dank für Ihre Bestellung"
        sender = "BESTSECRET <noreply@service.bestsecret.com>"
        text = (
            "BESTSECRET Bestellbestätigung. Wir beginnen nun mit den Vorbereitungen, "
            "damit Ihr Paket so schnell wie möglich versendet werden kann. "
            "Es wird voraussichtlich innerhalb von 2-5 Werktagen bei Ihnen eintreffen. "
            "Bestellnummer: 2302315200"
        )

        self.assertEqual(delivery_detection.classify_delivery_status(subject, "", text), "ordered")
        self.assertEqual(delivery_detection.delivery_display_title(subject, sender, text), "BestSecret #2302315200")
        self.assertEqual(delivery_detection.extract_delivery_eta(text), "in 2-5 Werktagen")
        self.assertEqual(
            delivery_detection.normalize_delivery_key(subject, sender, text),
            "service.bestsecret.com:order:2302315200",
        )

    def test_golighter_prescription_is_ordered_not_false_shipped(self):
        subject = "Dein Rezept wurde ausgestellt für: O-2026-121508957"
        sender = "Dein GoLighter Team <kontakt@golighter.de>"
        text = (
            "Versandvorbereitung: Die Apotheke bereitet Deine Bestellung für den Versand vor. "
            "Sobald Dein Paket verschickt wurde, erhältst Du eine E-Mail mit einem Link zur DHL-Sendungsverfolgung. "
            "Bestellnummer: O-2026-121508957. "
            "Deine nächste automatische Lieferung mit Wegovy 2,4 mg kommt voraussichtlich am 03.07.26."
        )

        self.assertEqual(delivery_detection.classify_delivery_status(subject, "", text), "ordered")
        self.assertEqual(
            delivery_detection.normalize_delivery_key(subject, sender, text),
            "golighter.de:order:O-2026-121508957",
        )
        self.assertEqual(delivery_detection.extract_delivery_eta_end_date(NOW, text), "2026-07-03")

    def test_golighter_auto_delivery_notice_is_noise(self):
        self.assertTrue(
            delivery_detection.is_delivery_noise(
                "Die nächsten Lieferungen kommen automatisch!",
                "Dein Behandlungsplan folgt einem festen 26-Tage-Rhythmus.",
                "Bevor die Lieferung verschickt wird, kannst Du sie stoppen.",
            )
        )

    def test_golighter_and_dhl_tracking_number_share_topic_key(self):
        tracking = "00340434664138415176"
        golighter_key = delivery_detection.normalize_delivery_key(
            "Dein Medikament ist auf dem Weg zu Dir.",
            "Dein GoLighter Team <kontakt@golighter.de>",
            f"Die Sendungsnummer für Deine Bestellung lautet {tracking}.",
        )
        dhl_key = delivery_detection.normalize_delivery_key(
            "Ihre Wellster Sendung kommt heute",
            "DHL Paket <noreply@dhl.de>",
            f"Sendungsstatus einsehen https://www.dhl.de/?piececode={tracking}",
        )

        self.assertEqual(golighter_key, f"tracking:{tracking}")
        self.assertEqual(dhl_key, golighter_key)

    def test_tracking_group_uses_newer_more_specific_status(self):
        tracking_key = "tracking:00340434664138415176"
        items = [
            delivery_item(
                subject="GoLighter/Wellster Medikament",
                status="shipped",
                status_rank=delivery_detection.delivery_status_rank("shipped"),
                topic_key=tracking_key,
                sort_key=internal_date(18),
            ),
            delivery_item(
                subject="GoLighter/Wellster Medikament",
                snippet="in Zustellung per DHL, kommt heute",
                status="out_for_delivery",
                status_rank=delivery_detection.delivery_status_rank("out_for_delivery"),
                topic_key=tracking_key,
                sort_key=internal_date(1),
            ),
        ]

        result = delivery_detection.summarize_delivery_candidates(items, NOW)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "out_for_delivery")

    def test_dhl_wellster_delivered_patterns_close_tracking_group(self):
        tracking_key = "tracking:00340434664137019238"
        items = [
            delivery_item(
                subject="GoLighter/Wellster Medikament",
                status="shipped",
                status_rank=delivery_detection.delivery_status_rank("shipped"),
                topic_key=tracking_key,
                sort_key=internal_date(24),
            ),
            delivery_item(
                subject="GoLighter/Wellster Medikament",
                snippet="geliefert",
                status=delivery_detection.classify_delivery_status(
                    "Ihre Wellster Sendung liegt nebenan",
                    "",
                    "Ihre Wellster Sendung ist angekommen. Wir haben sie abgegeben.",
                ),
                status_rank=delivery_detection.delivery_status_rank("delivered"),
                topic_key=tracking_key,
                sort_key=internal_date(2),
            ),
        ]

        self.assertEqual(delivery_detection.summarize_delivery_candidates(items, NOW), [])

    def test_numeric_delivery_dates_from_carrier_mail_are_parsed(self):
        dhl_text = "Ihre BESTSECRET Sendung wird Ihnen voraussichtlich am Mittwoch, den 01.07. zugestellt."
        hermes_text = "Voraussichtliche Zustellung am Dienstag, 11.11.2025"

        self.assertEqual(delivery_detection.extract_delivery_eta_end_date(NOW, dhl_text), "2026-07-01")
        self.assertEqual(delivery_detection.extract_delivery_eta(dhl_text), "Zustellung 01.07.")
        self.assertEqual(delivery_detection.extract_delivery_eta_end_date(NOW, hermes_text), "2025-11-11")

    def test_dhl_bestsecret_tracking_mail_stays_open_until_eta(self):
        now = dt.datetime(2026, 7, 2, 6, 2, tzinfo=ZoneInfo("Europe/Berlin"))
        messages = [
            {
                "from": "DHL Paket <noreply@dhl.de>",
                "subject": "Ihre BESTSECRET Sendung ist unterwegs",
                "snippet": (
                    "Ihre BESTSECRET Sendung wurde von uns bearbeitet und wird Ihnen "
                    "voraussichtlich am Freitag, den 03.07. zugestellt."
                ),
                "body": (
                    "Hallo Christian Galler, Ihre BESTSECRET Sendung wurde von uns bearbeitet "
                    "und wird Ihnen voraussichtlich am Freitag, den 03.07. zugestellt. "
                    "Sendungsstatus einsehen 00340434515530000000 "
                    "https://custcomm.dhl.de/go/?piececode=00340434515530000000"
                ),
                "sort_key": int(dt.datetime(2026, 7, 1, 18, 22, tzinfo=ZoneInfo("Europe/Berlin")).timestamp() * 1000),
            }
        ]

        result = delivery_detection.detect_open_deliveries(messages, now)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subject"], "BestSecret Sendung")
        self.assertEqual(result[0]["status"], "shipped")
        self.assertEqual(result[0]["eta_end_date"], "2026-07-03")
        self.assertIn("Zustellung 03.07.", result[0]["snippet"])

    def test_dhl_bestsecret_subject_is_enough_when_body_is_sparse(self):
        now = dt.datetime(2026, 7, 2, 18, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        result = delivery_detection.detect_open_deliveries(
            [
                {
                    "from": "DHL Paket <noreply@dhl.de>",
                    "subject": "Ihre BESTSECRET Sendung ist unterwegs",
                    "snippet": "Wichtige Informationen zu Ihrer Sendung",
                    "sort_key": int(
                        dt.datetime(2026, 7, 1, 18, 22, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
                        * 1000
                    ),
                }
            ],
            now,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subject"], "BestSecret Sendung")
        self.assertEqual(result[0]["status"], "shipped")

    def test_amazon_shipped_status_bar_is_not_delivered(self):
        now = dt.datetime(2026, 7, 2, 18, 0, tzinfo=ZoneInfo("Europe/Berlin"))
        body = (
            "Dein Paket wurde versendet! Bestellt Versendet In Zustellung Zugestellt "
            "Ankunft morgen Bestellnr. 305-2157751-6256325 Lieferung verfolgen "
            "Fliegengitter Balkontür Magnet"
        )

        result = delivery_detection.detect_open_deliveries(
            [
                {
                    "from": '"Amazon.de" <versandbestaetigung@amazon.de>',
                    "subject": "Versendet: „Fliegengitter Balkontür...“",
                    "snippet": "Versendet: „Fliegengitter Balkontür...“",
                    "body": body,
                    "sort_key": int(
                        dt.datetime(2026, 7, 2, 14, 32, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()
                        * 1000
                    ),
                }
            ],
            now,
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "shipped")
        self.assertIn("Amazon #305-2157751-6256325", result[0]["subject"])

    def test_public_detector_api_accepts_normalized_messages(self):
        messages = [
            {
                "from": "BESTSECRET <noreply@service.bestsecret.com>",
                "subject": "Vielen Dank für Ihre Bestellung",
                "snippet": "BESTSECRET Bestellbestätigung",
                "body": (
                    "Wir beginnen nun mit den Vorbereitungen, damit Ihr Paket so schnell wie möglich "
                    "versendet werden kann. Es wird voraussichtlich innerhalb von 2-5 Werktagen "
                    "bei Ihnen eintreffen. Bestellnummer: 2302315200"
                ),
                "sort_key": internal_date(2),
            }
        ]

        result = delivery_detection.detect_open_deliveries(messages, NOW)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subject"], "BestSecret #2302315200")
        self.assertEqual(result[0]["status"], "ordered")


if __name__ == "__main__":
    unittest.main()
