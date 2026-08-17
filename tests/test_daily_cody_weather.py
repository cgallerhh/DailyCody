import sys
import unittest
import datetime as dt
import io
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import daily_cody  # noqa: E402


class WeatherSummaryTest(unittest.TestCase):
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
