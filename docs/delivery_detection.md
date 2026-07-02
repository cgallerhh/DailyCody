# Liefererkennung in Daily Cody

Diese Routine ist die verbindliche Logik für offene Lieferungen aus Gmail. Neue Agenten sollen sie erweitern, nicht durch einzelne Ad-hoc-Suchen ersetzen.

## Nutzung als Modul

Die wiederverwendbare Logik liegt in `src/delivery_detection.py`.

```python
import datetime as dt
import delivery_detection

open_deliveries = delivery_detection.detect_open_deliveries(
    messages,
    dt.datetime.now().astimezone(),
    completed_topics=["Amazon #305-1314679-9745914"],
)
```

`messages` ist eine Liste normalisierter Mail-Dicts. Relevante Felder:

- `from`
- `subject`
- `snippet`
- `body` oder `text`
- `date`
- `thread_id`
- `sort_key` oder `internal_date` als Gmail-Millisekundenwert

Die Ausgabe ist eine Liste offener Lieferungen mit `subject`, `snippet`, `status`, `tracking_links`, `eta_end_date` und Metadaten.

## Nutzung als CLI

```bash
python3 scripts/detect_deliveries.py mails.json --now 2026-07-01T08:00:00+02:00
```

Eingabeformat:

```json
{
  "messages": [
    {
      "from": "DHL Paket <noreply@dhl.de>",
      "subject": "Ihre Wellster Sendung kommt heute",
      "snippet": "Wichtige Informationen zu Ihrer Sendung",
      "body": "Ihre Wellster Sendung wird Ihnen heute zugestellt. Sendungsstatus einsehen https://www.dhl.de/...",
      "sort_key": 1782887433000
    }
  ],
  "completed_topics": []
}
```

## Suchstrategie

Daily Cody sucht mehrstufig:

- allgemeine Liefer- und Statusbegriffe der letzten 60 Tage
- Amazon-spezifische Mails von `amazon.de`/`amazon.com`
- BestSecret-spezifische Mails, inklusive Service- und Carrier-Partner-Absender
- GoLighter/Wellster-spezifische Mails, inklusive Rezept-, Versand- und DHL-Meldungen
- Carrier-Mails von DHL, Hermes, DPD, UPS und GLS

Die Suchläufe werden dedupliziert, danach wird jede Mail vollständig gelesen und erst dann klassifiziert.

## Klassifikation

Eine Mail zählt nur als Lieferung, wenn sie nach Body-Auswertung in einen Status fällt:

- `ordered`: Bestellung bestätigt, Rezept ausgestellt, Versandvorbereitung
- `shipped`: Versandbereit, unterwegs, Sendungsnummer oder Tracking vorhanden
- `out_for_delivery`: kommt heute, in Zustellung
- `delivered`: zugestellt, geliefert, angekommen, liegt nebenan, abgegeben

Reine Info-Mails, Umfragen, Retourenstatus, HVV-Tickets, Behördenpostfach-Meldungen und eigene Cody-/Self-Mails sind explizit ausgeschlossen.

## Gruppierung

Wenn eine Sendungsnummer vorhanden ist, ist sie der stärkste Schlüssel. Dadurch werden GoLighter-Versandmail und DHL-Wellster-Zustellmail zusammengeführt. Danach folgen Bestellnummern, Produktnamen und erst zuletzt normalisierte Betreffzeilen.

Delivered-Mails schließen ältere offene Status derselben Gruppe. Veraltete offene Mails werden anhand von ETA und Alter ausgeblendet.

## Erledigt-Suppression

Manuelle Cody-Hinweise dürfen nur spezifische Lieferungen schließen. Generische Topics wie `Sendung`, `Paket`, `Bestellung`, `Amazon`, `DHL` oder `BestSecret` werden ignoriert, damit alte oder zu breite Erledigt-Mails keine aktuellen Lieferungen global ausblenden. Verlässlich geschlossen wird über Bestellnummer, Trackingnummer oder einen ausreichend spezifischen Produkt-/Liefertext.

## Bekannte Händler-Muster

- Amazon: Status im Betreff, Bestellnummer im Body, Trackinglink `progress-tracker`.
- BestSecret: generischer Betreff `Vielen Dank für Ihre Bestellung`, Bestellnummer im Body, ETA oft `2-5 Werktage`; spätere Mails `Ihre Bestellung ist versandbereit` oder Carrier-Mails wie `Ihre BESTSECRET Sendung ist unterwegs`.
- GoLighter/Wellster: Rezeptmail ist nur `ordered`; echte Versandmail enthält Sendungsnummer oder `Sendung verfolgen`; DHL-Wellster-Mails liefern `unterwegs`, `kommt heute` und `liegt nebenan`.
