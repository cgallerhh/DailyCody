#!/usr/bin/env python3
"""Render a local weather-only preview from a saved DWD KMZ; never send mail."""

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import daily_cody


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kmz", type=Path)
    parser.add_argument("--at", required=True, help="ISO timestamp with timezone offset")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    now = dt.datetime.fromisoformat(args.at.replace("Z", "+00:00"))
    if now.tzinfo is None:
        parser.error("--at requires an explicit timezone offset")
    now = now.astimezone(ZoneInfo("Europe/Berlin"))
    config = SimpleNamespace(timezone="Europe/Berlin", weather_label="21077 Hamburg-Harburg")
    # The production parser and renderer consume the saved source unchanged.
    # Warnings are intentionally absent from this offline layout-only preview.
    with patch.object(daily_cody.dt, "datetime", wraps=dt.datetime) as clock, \
         patch.object(daily_cody, "request_bytes", return_value=args.kmz.read_bytes()), \
         patch.object(daily_cody, "list_weather_warnings", return_value=[]):
        clock.now.return_value = now
        weather = daily_cody.get_weather(config)
    text = (
        f"# Daily Cody — {daily_cody.format_long_german_date(now)}\n\n"
        "Layoutvorschau · Wetter aus gespeicherter DWD-Vorhersage; Warnungen nicht abgerufen\n\n"
        f"## Today\n- {weather['summary']}\n"
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "DailyCody-Wettervorschau.html").write_text(daily_cody.markdown_to_basic_html(text, weather), encoding="utf-8")
    (args.output_dir / "DailyCody-Wettervorschau.txt").write_text(text, encoding="utf-8")
    (args.output_dir / "weather-preview-data.json").write_text(json.dumps(weather, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote weather preview to {args.output_dir}; no email sent.")


if __name__ == "__main__":
    main()
