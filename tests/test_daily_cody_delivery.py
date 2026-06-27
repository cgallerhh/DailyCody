import datetime as dt
import sys
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


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
        "status_rank": daily_cody.delivery_status_rank("shipped"),
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
                status_rank=daily_cody.delivery_status_rank("delivered"),
            )
        ]

        self.assertEqual(daily_cody.summarize_delivery_candidates(items, NOW), [])

    def test_delivered_update_suppresses_older_shipping_mail(self):
        items = [
            delivery_item(snippet="versendet", sort_key=internal_date(24)),
            delivery_item(
                snippet="geliefert",
                status="delivered",
                status_rank=daily_cody.delivery_status_rank("delivered"),
                sort_key=internal_date(2),
            ),
        ]

        self.assertEqual(daily_cody.summarize_delivery_candidates(items, NOW), [])

    def test_shipping_mail_with_past_eta_is_stale(self):
        items = [delivery_item(snippet="versendet, Zustellung 18. Juni-19. Juni", eta_end_date="2026-06-19")]

        self.assertEqual(daily_cody.summarize_delivery_candidates(items, NOW), [])
        self.assertEqual(
            daily_cody.extract_delivery_eta_end_date(NOW, "versendet, Zustellung 18. Juni-19. Juni"),
            "2026-06-19",
        )

    def test_shipping_mail_with_future_eta_stays_visible(self):
        items = [delivery_item(snippet="versendet, Zustellung 28. Juni-29. Juni", eta_end_date="2026-06-29")]

        result = daily_cody.summarize_delivery_candidates(items, NOW)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["subject"], "Amazon #305-1314679-9745914")

    def test_bundesagentur_notifications_are_not_deliveries(self):
        self.assertTrue(
            daily_cody.is_delivery_noise(
                "neue mitteilungen ihrer bundesagentur für arbeit",
                "",
                "In Ihrem Postfach wurde ein neues Dokument zugestellt.",
            )
        )

    def test_manual_cody_completion_topics_are_extracted(self):
        topics = daily_cody.extract_completed_delivery_topics_from_text(
            "Cody Lieferung erhalten: Amazon #305-1314679-9745914\n"
            "Cody, Fix Foxi Album ist angekommen"
        )

        self.assertIn("Amazon #305-1314679-9745914", topics)
        self.assertIn("Fix Foxi Album", topics)
        self.assertTrue(
            daily_cody.completed_delivery_topic_matches(
                "Amazon #305-1314679-9745914",
                "Amazon #305-1314679-9745914 versendet",
                daily_cody.normalize_status_text("Amazon #305-1314679-9745914 versendet"),
            )
        )


if __name__ == "__main__":
    unittest.main()
