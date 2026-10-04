# Weather paragraph above the existing card

The HTML email adds a responsive, rounded teal-blue text panel above the
unchanged three-column DWD weather card. Its selectable white paragraph is
generated deterministically from the same MOSMIX KMZ. No extra service, AI call,
credential, scheduling change, or email dispatch is needed.

The plain-text weather bullet includes the identical narrative, the original
daypart measurements and the forecast issue time in the configured timezone.
The existing numeric card and weather warnings remain in HTML.

## Source semantics

- `Neff` (effective cloud cover, percent), falling back to `N` (total cloud
  cover), provides sky descriptions. The application uses the mean of the
  available 06:00–18:00 slots: up to 25% → predominantly sunny; at least 75% →
  predominantly cloudy; otherwise → sunshine and clouds. These are editorial
  approximations, not official DWD description thresholds. Missing/invalid
  cloud values omit sky wording. Incomplete daytime coverage is explicitly
  qualified as the available forecast window. Fog suppresses sunshine wording
- `ww` supplies precipitation/fog type at forecast times. Unknown codes do not
  become generic fair weather. The code does not interpret `ww=0..3` as
  another provider's sky-condition scale. MOSMIX freezing fog uses 49
- `R101` is the hourly probability of precipitation exceeding 0.1 mm, not a
  whole-day probability or a rain-only probability. When a precipitation type
  is missing, the paragraph can report the maximum hourly probability for a
  daypart. Low probabilities never imply certainty of dry weather
- `FF` and `FX1` are converted from m/s to km/h. Their maximum daytime values
  are stated numerically; there is no unsupported claim that wind is rising
- `TTT` is converted from kelvin to Celsius. The nearest forecast slot within
  one hour of the run time is labelled explicitly as a forecast for that
  local time. MOSMIX does not provide a measured current temperature. A stale
  product (over 24 hours old) is marked and never supplies a current-like slot
- `TX` at 18 UTC supplies the daytime maximum recommended by DWD for Europe.
  It describes the preceding 12 hours, 06–18 UTC, so the wording is “Tagsüber
  werden bis zu … Grad erwartet”. If absent, the maximum available hourly TTT
  is clearly labelled as the maximum in the forecast window
- The forecast's valid date and its issue timestamp remain distinct. UTC
  timestamps are retained for nearest-slot selection, including repeated
  local hours during the autumn DST transition. After 19:00, the existing
  next-day selection remains in place; the paragraph says “Morgen”

Missing values remain absent or “k. A.”. NaN/Infinity are treated as missing
through parsing, aggregation and rendering. Missing issue times are visible.
If the entire source cannot be downloaded or parsed, existing fail-closed
behavior is unchanged; no invented replacement forecast is sent.

Official references:

- [DWD field catalog](https://opendata.dwd.de/weather/lib/MetElementDefinition.xml)
- [DWD MOSMIX procedure, especially §§9.5–9.6](https://www.dwd.de/DE/leistungen/met_verfahren_mosmix/mosmix_verfahrenbeschreibung_gesamt.pdf?__blob=publicationFile&v=6)
- [DWD weather-code workbook](https://www.dwd.de/DE/leistungen/opendata/help/schluessel_datenformate/kml/mosmix_element_weather_xls.xlsx?__blob=publicationFile&v=6)
- [DWD missing-data guidance](https://www.dwd.de/DE/leistungen/met_verfahren_mosmix/faq/daten_fehlen.html)

## Offline preview

Save an actual DWD MOSMIX KMZ, then render it without credentials or network:

```sh
python3 scripts/preview_weather.py /path/to/forecast.kmz \
  --at 2026-10-02T07:40:00+02:00 --output-dir /tmp/cody-weather-preview
```

This writes HTML, plain text and the structured source-derived weather data.
It does not send email. Because the preview is offline, warnings are not fetched
and this limitation is visibly labelled in the preview. Production warnings
are unchanged.

The HTML uses an email presentation table, inline color/padding/font styles and
a `bgcolor` fallback. A small media query reduces text size on narrow screens;
the fluid layout also works without media-query support. Mail clients that do
not support border-radius may show square corners. Browser/static render checks
do not replace a real Apple Mail/Gmail/Outlook inbox test.

## Tests

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q src tests scripts
git diff --check
```

Regressions cover source conversion, precipitation timing, conservative missing
data, nonfinite numbers, unknown codes, fog, nighttime/next-day selection,
staleness, TX vs hourly maxima, nearest temperature slot, DST, HTML escaping,
and matching HTML/plain-text weather paragraphs.
