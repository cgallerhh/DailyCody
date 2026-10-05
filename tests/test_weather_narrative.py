import base64
import datetime as dt
import email
from email import policy
import html
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody
import weather_narrative


ZONE = ZoneInfo("Europe/Berlin")
NOW = dt.datetime(2026, 10, 2, 7, 40, tzinfo=ZONE)


def forecast(**changes):
    result = {
        "time": [f"2026-10-02T{h:02d}:00" for h in range(6, 21)],
        "temperature_2m": [12, 13, 14, 15, 16, 17, 18, 19, 20, 20, 19, 18, 17, 16, 15],
        "temperature_max_12h": [None] * 14 + [21],
        "cloud_cover_effective": [45] * 15,
        "weather_code": [3] * 9 + [80] * 4 + [3, 3],
        "precipitation_probability": [5] * 9 + [70] * 4 + [5, 5],
        "wind_speed_10m": [20] * 15,
        "wind_gusts_10m": [38] * 15,
    }
    result.update(changes)
    return result


def overview(hourly=None, **kwargs):
    args = dict(forecast_date=NOW.date(), now=NOW, timezone="Europe/Berlin", issued_at="2026-10-02T03:00:00Z")
    args.update(kwargs)
    return weather_narrative.build_overview(hourly if hourly is not None else forecast(), **args)


class WeatherNarrativeTest(unittest.TestCase):
    def test_reference_shape_comes_from_actual_fields(self):
        result = overview()
        text = result["narrative"]
        self.assertIn("Sonne und Wolken", text)
        self.assertIn("Nachmittags sind Regenschauer vorhergesagt.", text)
        self.assertIn("20 km/h, in Böen bis zu 38 km/h", text)
        self.assertIn("Für 08:00 Uhr sind 14 Grad vorhergesagt", text)
        self.assertIn("Tagsüber werden bis zu 21 Grad erwartet", text)
        self.assertEqual(result["current_forecast_at"], "2026-10-02T08:00:00+02:00")
        self.assertEqual(result["high_c"], 21)
        self.assertNotIn("aktuell", text)
        self.assertIn("Stand 02.10.2026, 05:00 Uhr", result["narrative_source"])
        self.assertIn("Europe/Berlin", result["narrative_source"])

    def test_missing_optional_fields_never_invent_conditions(self):
        result = overview(forecast(weather_code=[], cloud_cover_effective=[], cloud_cover=[], wind_gusts_10m=[]))
        self.assertNotIn("Sonne", result["narrative"])
        self.assertNotIn("Schauer", result["narrative"])
        self.assertNotIn("Böen", result["narrative"])
        self.assertIn("stündliche Niederschlagswahrscheinlichkeit", result["narrative"])
        self.assertIn("70 Prozent", result["narrative"])

    def test_ww_sky_codes_do_not_replace_missing_cloud_data(self):
        for code in (0, 1, 2, 3):
            with self.subTest(code=code):
                text = overview(forecast(weather_code=[code] * 15, cloud_cover_effective=[]))["narrative"]
                self.assertNotIn("Sonne", text)
                self.assertNotIn("Wolken", text)

    def test_n_fallback_and_fog_suppress_sunshine(self):
        cloudy = forecast(cloud_cover_effective=[], cloud_cover=[90] * 15)
        self.assertIn("dichte Wolken", overview(cloudy)["narrative"])
        fog = forecast(cloud_cover_effective=[0] * 15, weather_code=[49] * 15)
        text = overview(fog)["narrative"]
        self.assertNotIn("Sonne", text)
        self.assertIn("Nebel", text)

    def test_rain_is_singular_and_unknown_codes_do_not_become_showers(self):
        text = overview(forecast(weather_code=[61] * 15))["narrative"]
        self.assertIn("Vormittags ist Regen vorhergesagt", text)
        text = overview(forecast(weather_code=[999] * 15))["narrative"]
        self.assertNotIn("Regenschauer", text)

    def test_empty_and_nonfinite_data_fall_back_without_crashing(self):
        for hourly in ({}, forecast(temperature_2m=[float("nan")] * 15,
                                   temperature_max_12h=[float("inf")] * 15,
                                   cloud_cover_effective=[float("nan")] * 15,
                                   weather_code=[float("inf")] * 15,
                                   wind_speed_10m=[float("nan")] * 15,
                                   wind_gusts_10m=[float("inf")] * 15,
                                   precipitation_probability=[None] * 15)):
            result = overview(hourly)
            self.assertIn("keine ausreichenden Wetterdaten", result["narrative"])
            self.assertIsNone(result["current_temp_c"])
            self.assertIsNone(result["high_c"])

    def test_old_first_slot_is_not_current_and_distant_slot_is_omitted(self):
        result = overview(forecast())
        self.assertEqual(result["current_temp_c"], 14)
        result = overview(forecast(time=["2026-10-02T12:00"], temperature_2m=[22]))
        self.assertIsNone(result["current_temp_c"])
        self.assertNotIn("Für 12:00 Uhr", result["narrative"])

    def test_missing_tx_uses_honest_forecast_window_maximum(self):
        result = overview(forecast(temperature_max_12h=[]))
        self.assertEqual(result["high_c"], 20)
        self.assertIn("Im Vorhersagezeitraum für heute", result["narrative"])
        self.assertNotIn("Tageshöchst", result["narrative"])

    def test_06_utc_tx_is_not_the_daytime_maximum(self):
        values = [None] * 15
        values[2], values[14] = 30, 21
        self.assertEqual(overview(forecast(temperature_max_12h=values))["high_c"], 21)

    def test_evening_tomorrow_has_no_today_temperature(self):
        now = NOW.replace(day=1, hour=21)
        result = overview(now=now)
        self.assertTrue(result["narrative"].startswith("Morgen"))
        self.assertNotIn("Für 08:00", result["narrative"])

    def test_stale_forecast_is_marked_and_not_current(self):
        result = overview(issued_at="2026-09-30T03:00:00Z")
        self.assertTrue(result["forecast_stale"])
        self.assertTrue(result["narrative"].startswith("Hinweis:"))
        self.assertIsNone(result["current_temp_c"])

    def test_missing_issue_time_is_visible(self):
        result = overview(issued_at="invalid")
        self.assertIn("Ausgabezeitpunkt", result["narrative"])
        self.assertIn("Stand unbekannt", result["narrative_source"])

    def test_one_remaining_hour_does_not_become_a_whole_day_claim(self):
        result = overview(forecast(time=["2026-10-02T18:00"], cloud_cover_effective=[0]))
        self.assertIn("Im verfügbaren Vorhersagezeitraum", result["narrative"])
        self.assertNotIn("Heute überwiegt", result["narrative"])

    def test_nonfinite_values_are_safe_in_full_get_weather_path(self):
        hourly = forecast(temperature_2m=[float("nan")] * 15,
                          temperature_max_12h=[float("inf")] * 15,
                          wind_speed_10m=[float("inf")] * 15)
        data = dict(hourly=hourly, station_id="C720", station_name="Hamburg", issued_at="2026-10-02T03:00Z")
        with patch.object(daily_cody.dt, "datetime", wraps=dt.datetime) as clock, \
             patch.object(daily_cody, "request_bytes", return_value=b"fixture"), \
             patch.object(daily_cody, "parse_dwd_mosmix_kmz", return_value=data), \
             patch.object(daily_cody, "list_weather_warnings", return_value=[]):
            clock.now.return_value = NOW
            weather = daily_cody.get_weather(SimpleNamespace(timezone="Europe/Berlin", weather_label="Hamburg"))
        self.assertIn("k. A.", weather["summary"])
        self.assertIsNone(weather["high_c"])
        self.assertEqual(daily_cody.parse_dwd_mosmix_values("NaN inf -Inf - 2", 5), [None, None, None, None, 2.0])

    def test_dst_repeated_hour_uses_utc_instant(self):
        now = dt.datetime(2026, 10, 25, 2, 45, tzinfo=ZONE, fold=1)
        hourly = dict(time_utc=["2026-10-25T00:00Z", "2026-10-25T01:00Z", "2026-10-25T02:00Z"], temperature_2m=[8, 7, 6])
        result = overview(hourly, now=now, forecast_date=now.date(), issued_at="2026-10-24T21:00Z")
        self.assertEqual(result["current_temp_c"], 6)
        self.assertEqual(result["current_forecast_at"], "2026-10-25T03:00:00+01:00")

    def test_html_escapes_text_uses_email_table_and_keeps_existing_card(self):
        weather = {**overview(), "label": "Hamburg", "periods": daily_cody.build_weather_periods(forecast(), NOW.date())}
        weather["narrative"] += " <script>bad</script> & test"
        weather["summary"] = weather["narrative"] + " " + weather["narrative_source"]
        body = "# Daily Cody\n## Today\n- " + weather["summary"] + "\n- Termin"
        output = daily_cody.markdown_to_basic_html(body, weather)
        self.assertIn('role="presentation"', output)
        self.assertIn('bgcolor="#206887"', output)
        self.assertIn('border-radius:16px', output)
        self.assertIn(html.escape(weather["narrative"]), output)
        self.assertNotIn("<script>", output)
        self.assertIn("Wetter · Hamburg", output)
        self.assertIn("Vormittags", output)
        self.assertIn("Termin", output)
        self.assertIn('name="viewport"', output)
        self.assertEqual(output.count('class="weather-overview"'), 1)

    def test_get_weather_and_both_email_parts_include_same_narrative(self):
        data = dict(hourly=forecast(), station_id="C720", station_name="Hamburg", issued_at="2026-10-02T03:00Z")
        with patch.object(daily_cody.dt, "datetime", wraps=dt.datetime) as clock, \
             patch.object(daily_cody, "request_bytes", return_value=b"fixture"), \
             patch.object(daily_cody, "parse_dwd_mosmix_kmz", return_value=data), \
             patch.object(daily_cody, "list_weather_warnings", return_value=[]):
            clock.now.return_value = NOW
            weather = daily_cody.get_weather(SimpleNamespace(timezone="Europe/Berlin", weather_label="Hamburg"))
        body = "# Daily Cody\n## Today\n- " + weather["summary"]
        with patch.object(daily_cody, "request_json") as send:
            daily_cody.send_email(SimpleNamespace(sender="a@example.com", recipient="b@example.com"), "fake", "Test", body, weather)
        message = email.message_from_bytes(base64.urlsafe_b64decode(send.call_args.kwargs["body"]["raw"]), policy=policy.default)
        for kind in ("plain", "html"):
            part = message.get_body(preferencelist=(kind,)).get_content()
            self.assertIn(weather["narrative"], part)
            self.assertIn(weather["narrative_source"], part)
        self.assertIn(weather["measurement_summary"], message.get_body(preferencelist=("plain",)).get_content())

    def test_weather_overview_matches_compact_briefing_type_without_media_query(self):
        weather = {"narrative": "Für 06:00 Uhr sind 9 Grad vorhergesagt.", "narrative_source": "DWD · 05.10.2026"}
        panel = daily_cody.render_weather_overview(weather)
        self.assertIn("font-size:14px;line-height:1.4", panel)
        self.assertIn("padding:14px 16px", panel)
        self.assertIn("margin:8px 0 0;font-size:11px", panel)
        self.assertIn("-webkit-text-size-adjust:100%", panel)
        self.assertNotIn("font-size:23px", panel)
        self.assertNotIn("font-size:20px", panel)
        self.assertIn(weather["narrative"], panel)
        self.assertIn(weather["narrative_source"], panel)

    def test_auto_detected_weather_links_are_scoped_and_other_links_stay_normal(self):
        weather = {"narrative": "Für 06:00 Uhr sind 9 Grad vorhergesagt.", "summary": "Weather"}
        output = daily_cody.markdown_to_basic_html(
            "# Daily Cody\n## Today\n- Weather\n- [Termin](https://example.com/appointment)", weather
        )
        self.assertIn(".weather-overview a { color:inherit !important; text-decoration:none !important;", output)
        self.assertNotIn("@media", output)
        self.assertNotIn("font-size:20px !important", output)
        self.assertIn('<a href="https://example.com/appointment" style="color:#1a73e8;text-decoration:none">Termin</a>', output)

    def test_kmz_optional_fields_and_utc_are_preserved(self):
        xml = '''<kml xmlns="http://www.opengis.net/kml/2.2" xmlns:dwd="https://opendata.dwd.de/weather/lib/pointforecast_dwd_extension_V1_0.xsd"><Document><ExtendedData><dwd:IssueTime>2026-10-02T03:00Z</dwd:IssueTime><dwd:TimeStep>2026-10-02T18:00Z</dwd:TimeStep></ExtendedData><Placemark><name>C720</name><ExtendedData>'''
        for key, value in {"TTT": "290.15", "FF": "2", "FX1": "4", "R101": "60", "N": "80", "Neff": "65", "ww": "80", "TX": "294.15"}.items():
            xml += f'<dwd:Forecast dwd:elementName="{key}"><dwd:value>{value}</dwd:value></dwd:Forecast>'
        xml += '</ExtendedData></Placemark></Document></kml>'
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("data.kml", xml)
        hourly = daily_cody.parse_dwd_mosmix_kmz(payload.getvalue(), "Europe/Berlin")["hourly"]
        self.assertEqual(hourly["time_utc"], ["2026-10-02T18:00Z"])
        self.assertEqual(hourly["weather_code"], [80])
        self.assertEqual(hourly["cloud_cover_effective"], [65])
        self.assertEqual(hourly["cloud_cover"], [80])
        self.assertEqual(hourly["temperature_max_12h"], [21])
        self.assertEqual(hourly["wind_gusts_10m"], [14.4])
