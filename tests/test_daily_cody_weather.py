import sys
import unittest
import datetime as dt
import base64
import email
from email import policy
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


class WeatherSummaryTest(unittest.TestCase):
    def test_evening_weather_uses_tomorrow_and_labels_its_date(self):
        now = dt.datetime(2026, 9, 29, 21, tzinfo=ZoneInfo("Europe/Berlin"))
        hourly = {
            "time": ["2026-09-29T22:00", "2026-09-30T09:00"],
            "temperature_2m": [12, 16],
            "precipitation_probability": [80, 10],
            "wind_speed_10m": [20, 7],
        }
        data = {"hourly": hourly, "station_id": "C720", "station_name": "Hamburg", "issued_at": "now"}
        config = SimpleNamespace(timezone="Europe/Berlin", weather_label="Hamburg-Harburg")
        with patch.object(daily_cody.dt, "datetime", wraps=dt.datetime) as clock, \
             patch.object(daily_cody, "request_bytes", return_value=b"kmz"), \
             patch.object(daily_cody, "parse_dwd_mosmix_kmz", return_value=data), \
             patch.object(daily_cody, "list_weather_warnings", return_value=[]):
            clock.now.return_value = now
            weather = daily_cody.get_weather(config)

        self.assertEqual(weather["forecast_date"], "2026-09-30")
        self.assertEqual(weather["high_c"], 16)
        self.assertIn("30.09.2026", weather["summary"])
        self.assertIn("30.09.2026", daily_cody.render_weather_card(weather))

    def test_dwd_mosmix_kmz_parser_converts_units_and_utc_to_berlin_time(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <kml:kml xmlns:kml="http://www.opengis.net/kml/2.2"
          xmlns:dwd="https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd">
          <kml:Document><kml:ExtendedData><dwd:ProductDefinition>
            <dwd:IssueTime>2026-08-16T03:00:00Z</dwd:IssueTime>
            <dwd:ForecastTimeSteps>
              <dwd:TimeStep>2026-08-16T04:00:00Z</dwd:TimeStep>
              <dwd:TimeStep>2026-08-16T05:00:00Z</dwd:TimeStep>
            </dwd:ForecastTimeSteps>
          </dwd:ProductDefinition></kml:ExtendedData>
          <kml:Placemark><kml:name>C720</kml:name>
            <kml:description>HAMBURG-NEUWIEDENTH.</kml:description><kml:ExtendedData>
              <dwd:Forecast dwd:elementName="TTT"><dwd:value>293.15 294.15</dwd:value></dwd:Forecast>
              <dwd:Forecast dwd:elementName="R101"><dwd:value>10 30</dwd:value></dwd:Forecast>
              <dwd:Forecast dwd:elementName="FF"><dwd:value>2 3</dwd:value></dwd:Forecast>
              <dwd:Forecast dwd:elementName="RR1c"><dwd:value>- 0.4</dwd:value></dwd:Forecast>
            </kml:ExtendedData></kml:Placemark>
          </kml:Document>
        </kml:kml>'''
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("forecast.kml", xml)

        result = daily_cody.parse_dwd_mosmix_kmz(payload.getvalue(), "Europe/Berlin")

        self.assertEqual(result["station_id"], "C720")
        self.assertEqual(result["station_name"], "HAMBURG-NEUWIEDENTH.")
        self.assertEqual(result["hourly"]["time"], ["2026-08-16T06:00", "2026-08-16T07:00"])
        self.assertAlmostEqual(result["hourly"]["temperature_2m"][0], 20.0)
        self.assertAlmostEqual(result["hourly"]["wind_speed_10m"][1], 10.8)
        self.assertEqual(result["hourly"]["precipitation_probability"], [10.0, 30.0])

    def test_neutral_weather_uses_three_dayparts_and_measurements_only(self):
        times = [f"2026-08-16T{hour:02d}:00" for hour in range(24)]
        periods = daily_cody.build_weather_periods(
            {
                "time": times,
                "temperature_2m": list(range(10, 34)),
                "precipitation_probability": [hour * 3 for hour in range(24)],
                "wind_speed_10m": [hour + 5 for hour in range(24)],
            },
            dt.date(2026, 8, 16),
        )

        summary = daily_cody.build_neutral_weather_summary(
            "21077 Hamburg-Harburg", periods
        )

        self.assertEqual(
            summary,
            (
                "21077 Hamburg-Harburg — Vormittags: 16–21 °C, Regenwahrscheinlichkeit 33 %, Wind bis 16 km/h; "
                "mittags: 22–24 °C, Regenwahrscheinlichkeit 42 %, Wind bis 19 km/h; "
                "nachmittags: 25–28 °C, Regenwahrscheinlichkeit 54 %, Wind bis 23 km/h."
            ),
        )
        self.assertNotIn("Sonne", summary)
        self.assertNotIn("Schauer", summary)

    def test_weather_bullet_is_replaced_with_exact_neutral_summary(self):
        briefing = "# Daily Cody\n\n## Today\n- Heute scheint die Sonne.\n- Termin"
        summary = "21077 Hamburg-Harburg — Vormittags: 18 °C, Regenwahrscheinlichkeit 10 %, Wind bis 8 km/h."

        result = daily_cody.replace_weather_bullet(briefing, summary)

        self.assertIn(f"## Today\n- {summary}\n- Termin", result)
        self.assertNotIn("scheint die Sonne", result)

    def test_email_weather_card_uses_structured_values_and_preserves_plain_text(self):
        weather = {
            "label": "Hamburg-Harburg",
            "source": "DWD Open Data MOSMIX_L",
            "summary": "Hamburg-Harburg — Vormittags: 16–21 °C.",
            "periods": [
                {
                    "label": "Vormittags",
                    "temperature_min_c": 16,
                    "temperature_max_c": 21,
                    "rain_probability_pct": 33,
                    "wind_speed_kmh": 16,
                },
                {
                    "label": "Mittags",
                    "temperature_min_c": 22,
                    "temperature_max_c": 24,
                    "rain_probability_pct": 42,
                    "wind_speed_kmh": 19,
                },
                {
                    "label": "Nachmittags",
                    "temperature_min_c": 25,
                    "temperature_max_c": 28,
                    "rain_probability_pct": 54,
                    "wind_speed_kmh": 23,
                },
            ],
            "warnings": [],
        }
        markdown = (
            "# Daily Cody\n\n## Today\n- " + weather["summary"]
            + "\n- Termin\n\n## Deliveries\n- Paket — [Trackinglink](https://example.com/track)"
        )

        with patch.object(daily_cody, "request_json") as request:
            daily_cody.send_email(
                SimpleNamespace(sender="sender@example.com", recipient="reader@example.com"),
                "token",
                "Daily Cody",
                markdown,
                weather,
            )

        raw = request.call_args.kwargs["body"]["raw"]
        message = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertEqual(plain.strip(), markdown)
        self.assertIn("Wetter · Hamburg-Harburg", html_body)
        self.assertIn("16–21 °C", html_body)
        self.assertIn("Regen 33 %", html_body)
        self.assertIn("Wind 23 km/h", html_body)
        self.assertNotIn(weather["summary"], html_body)
        self.assertIn("https://example.com/track", html_body)
        self.assertIn("Termin", html_body)

    def test_weather_card_escapes_untrusted_values_and_handles_missing_metrics(self):
        html_body = daily_cody.render_weather_card(
            {
                "label": "<script>alert(1)</script>",
                "source": "DWD & Partner",
                "periods": [{"label": "Vormittags", "temperature_min_c": None}],
            }
        )

        self.assertIn("&lt;script&gt;", html_body)
        self.assertNotIn("<script>", html_body)
        self.assertIn("DWD &amp; Partner", html_body)
        self.assertIn("k. A.", html_body)
