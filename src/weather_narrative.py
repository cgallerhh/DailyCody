"""Source-bound German prose for the DWD MOSMIX email weather overview.

MOSMIX is a forecast, not a current observation. Unknown/missing weather codes
never imply sunshine or dry weather. All displayed times use the briefing zone.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Any
from zoneinfo import ZoneInfo


DAYPARTS = (("Vormittags", 6, 11), ("Mittags", 12, 14), ("Nachmittags", 15, 18))
PRECIPITATION = {
    51: "Nieselregen", 53: "Nieselregen", 55: "Nieselregen",
    56: "gefrierender Nieselregen", 57: "gefrierender Nieselregen",
    61: "Regen", 63: "Regen", 65: "Regen",
    66: "gefrierender Regen", 67: "gefrierender Regen",
    68: "Schneeregen", 69: "Schneeregen",
    71: "Schnee", 73: "Schnee", 75: "Schnee", 77: "Schneegriesel",
    80: "Regenschauer", 81: "Regenschauer", 82: "kräftige Regenschauer",
    83: "Schneeregenschauer", 84: "Schneeregenschauer",
    85: "Schneeschauer", 86: "Schneeschauer",
    95: "Gewitter",
}


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def local_time(value: Any, zone: ZoneInfo) -> dt.datetime | None:
    try:
        timestamp = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=zone)
        return timestamp.astimezone(zone)
    except (TypeError, ValueError):
        return None


def values_at(hourly: dict[str, Any], key: str, indexes: list[int]) -> list[float]:
    values = hourly.get(key) or []
    if not isinstance(values, list):
        return []
    result = [finite_number(values[i]) for i in indexes if i < len(values)]
    return [value for value in result if value is not None]


def joined(items: list[str]) -> str:
    return " und ".join(items) if len(items) < 3 else ", ".join(items[:-1]) + " und " + items[-1]


def build_overview(
    hourly: dict[str, Any], forecast_date: dt.date, now: dt.datetime,
    timezone: str, issued_at: str,
) -> dict[str, Any]:
    zone = ZoneInfo(timezone)
    now = now.astimezone(zone)
    # UTC preserves both repeated 02:00 slots across the autumn DST transition.
    timestamps = [local_time(value, zone) for value in (hourly.get("time_utc") or hourly.get("time", []))]
    indexes = [i for i, stamp in enumerate(timestamps) if stamp and stamp.date() == forecast_date]
    daytime = [i for i in indexes if 6 <= timestamps[i].hour <= 18]
    issue = local_time(issued_at, zone)
    stale = bool(issue and (now.timestamp() - issue.timestamp()) > 24 * 3600)
    day_word = "Heute" if forecast_date == now.date() else (
        "Morgen" if forecast_date == now.date() + dt.timedelta(days=1) else f"Am {forecast_date:%d.%m.%Y}"
    )
    sentences = []
    codes = values_at(hourly, "weather_code", daytime)
    clouds = [value for value in values_at(hourly, "cloud_cover_effective", daytime) if 0 <= value <= 100]
    if len(clouds) != len(daytime):
        clouds = [value for value in values_at(hourly, "cloud_cover", daytime) if 0 <= value <= 100]
    # N/Neff explicitly measure cloud cover; do not reinterpret ww=0..3 as
    # Open-Meteo/WMO sky classes. Skip sunshine wording when fog is forecast.
    if daytime and len(clouds) == len(daytime) and not any(code in {45, 49} for code in codes):
        covered_hours = {timestamps[i].hour for i in daytime}
        sky_prefix = day_word if set(range(6, 19)).issubset(covered_hours) else "Im verfügbaren Vorhersagezeitraum"
        mean_cloud = sum(clouds) / len(clouds)
        if mean_cloud <= 25:
            sentences.append(f"{sky_prefix} überwiegt voraussichtlich die Sonne.")
        elif mean_cloud >= 75:
            sentences.append(f"{sky_prefix} überwiegen voraussichtlich dichte Wolken.")
        else:
            sentences.append(f"{sky_prefix} gibt es voraussichtlich Sonne und Wolken.")

    for label, start, end in DAYPARTS:
        selected = [i for i in daytime if start <= timestamps[i].hour <= end]
        period_codes = values_at(hourly, "weather_code", selected)
        events = list(dict.fromkeys(PRECIPITATION[int(code)] for code in period_codes
                                    if code.is_integer() and int(code) in PRECIPITATION))
        if events:
            verb = "sind" if len(events) > 1 or events[0].endswith("schauer") else "ist"
            sentences.append(f"{label} {verb} {joined(events)} vorhergesagt.")
        elif any(code in {45, 49} for code in period_codes):
            sentences.append(f"{label} ist Nebel vorhergesagt.")
        else:
            probabilities = [value for value in values_at(hourly, "precipitation_probability", selected)
                             if 0 <= value <= 100]
            if probabilities and max(probabilities) >= 30:
                sentences.append(f"{label} liegt die stündliche Niederschlagswahrscheinlichkeit bei bis zu {round(max(probabilities))} Prozent.")

    # Report values, not ungrounded 'gusty' or 'freshening' interpretations.
    wind = [value for value in values_at(hourly, "wind_speed_10m", daytime) if value >= 0]
    gusts = [value for value in values_at(hourly, "wind_gusts_10m", daytime) if value >= 0]
    if wind:
        sentence = f"Der Wind erreicht bis zu {round(max(wind))} km/h"
        if gusts:
            sentence += f", in Böen bis zu {round(max(gusts))} km/h"
        sentences.append(sentence + ".")
    elif gusts:
        sentences.append(f"Windböen bis {round(max(gusts))} km/h sind vorhergesagt.")

    # A nearby model slot is explicitly labelled as a prediction at its valid
    # time, never as a measured current temperature or as the first KMZ value.
    nearby = [(abs(stamp.timestamp() - now.timestamp()), i) for i, stamp in enumerate(timestamps)
              if stamp and stamp.date() == now.date() and not stale
              and abs(stamp.timestamp() - now.timestamp()) <= 3600
              and values_at(hourly, "temperature_2m", [i])]
    current_index = min(nearby)[1] if nearby else None
    current = values_at(hourly, "temperature_2m", [current_index]) if current_index is not None else []
    current_at = timestamps[current_index] if current_index is not None else None
    # Restrict maximum to the actual forecast window, not an invented full-day
    # maximum when the model's earlier hours are no longer in the product.
    temperatures = values_at(hourly, "temperature_2m", indexes)
    if current and forecast_date == now.date():
        sentences.append(f"Für {current_at:%H:%M} Uhr sind {round(current[0])} Grad vorhergesagt.")
    # DWD recommends TX at 18 UTC as the daytime maximum for Europe.
    # TX is a preceding-12h maximum, not the maximum of hourly TTT slots.
    tx_indexes = [i for i in indexes if timestamps[i].astimezone(dt.timezone.utc).hour == 18]
    maxima = values_at(hourly, "temperature_max_12h", tx_indexes)
    high = max(maxima) if maxima else (max(temperatures) if temperatures else None)
    if maxima:
        sentences.append(f"Tagsüber werden bis zu {round(high)} Grad erwartet.")
    elif temperatures:
        sentences.append(f"Im Vorhersagezeitraum für {'heute' if forecast_date == now.date() else 'morgen' if forecast_date == now.date() + dt.timedelta(days=1) else forecast_date.strftime('%d.%m.%Y')} werden bis zu {round(max(temperatures))} Grad erwartet.")
    if not sentences:
        sentences.append(f"Für den {forecast_date:%d.%m.%Y} liegen keine ausreichenden Wetterdaten vor.")
    if stale:
        sentences.insert(0, "Hinweis: Diese Vorhersage ist älter als 24 Stunden.")
    elif issue is None:
        sentences.append("Der Ausgabezeitpunkt der Vorhersage fehlt.")

    source_note = f"Vorhersage für {forecast_date:%d.%m.%Y} · Quelle: DWD Open Data MOSMIX_L"
    if issue:
        source_note += f" · Stand {issue:%d.%m.%Y, %H:%M} Uhr"
    else:
        source_note += " · Stand unbekannt"
    source_note += f" · Ortszeit {timezone}"
    return {
        "narrative": " ".join(sentences), "narrative_source": source_note,
        "current_temp_c": current[0] if current else None,
        "current_forecast_at": current_at.isoformat() if current_at else None,
        "forecast_stale": stale, "high_c": high,
        "high_source": "DWD TX 18 UTC" if maxima else "hourly forecast maximum",
    }
