# Follow-Up-Monitor und Cody

Der bestehende Codex-Monitor laeuft montags bis freitags um 05:00 Uhr in
Europe/Berlin. Er nutzt weiterhin Gmail, Google Calendar und Outlook im
bestehenden Chat. Die Cody-Mail folgt gegen 06:00 Uhr.

Ein Cron auf demselben Mac loest weder Schlafzustand noch fehlende
Connector-Zugriffe. Ein unabhaengiger Serverlauf waere eine eigene Anwendung
mit Mail-/Kalender-Authentifizierung, persistentem Zustand und Fehlerueberwachung.

## Private Uebergabe

DailyCody ist ein oeffentliches Repository. Persoenliche Monitor-Ergebnisse
werden ausschliesslich als Actions-Secret `FOLLOW_UP_SNAPSHOT_JSON` uebergeben.
Sie duerfen nicht als Datei oder Log im Repository landen.
Die Einrichtung erfordert ausdrueckliche Zustimmung zu dieser Speicherung;
Workflows mit Secret-Zugriff koennen den Inhalt lesen. Ohne eingerichtete
Uebergabe nutzt Cody aktuelle persoenliche Gmail-Rueckmeldungen mit sichtbarem
Quellenhinweis als Ersatz. Das ist kein erfolgreicher Monitorlauf und enthaelt
keine Outlook- oder Kalender-Vergleichsergebnisse. Ohne relevante Gmail-Punkte
bleibt der Datenhinweis sichtbar statt einer falschen Entwarnung.

Der Monitor schreibt einen Kandidaten in seinen eigenen Arbeitsordner und
ruft `scripts/publish_follow_up_snapshot.py` auf. Das Skript prueft Format,
Quellenabdeckung, Quellenlinks und Zeitstempel. Es aktualisiert das Secret
ueber `gh secret set` mit dem Inhalt auf stdin. Erst nach erfolgreicher
GitHub-Bestaetigung ersetzt es den lokalen Vergleichsstand atomar.
Fehler lassen den bisherigen Vergleichsstand bestehen, damit der naechste
Lauf den Zeitraum erneut prueft.

Der lokale Vergleichsstand liegt im Monitor-Arbeitsordner unter
`state/baseline.json`; der Kandidat unter `state/candidate.json`.
Die alte Automationsdatei `memory.md` ist nur historische Lesereferenz.

## Snapshot-Vertrag

```json
{
  "schema_version": 1,
  "status": "complete",
  "checked_from": "2026-09-28T05:00:00+02:00",
  "checked_until": "2026-09-29T05:01:00+02:00",
  "generated_at": "2026-09-29T05:02:00+02:00",
  "coverage": {
    "gmail": "ok", "google_calendar": "ok",
    "outlook_mail": "ok", "outlook_calendar": "ok"
  },
  "items": [{
    "id": "example-reply",
    "topic": "Beispiel",
    "change": "Eine persoenliche Antwort ist eingegangen.",
    "relevance": "Die Rueckfrage ist noch offen.",
    "action": "Antwort pruefen.",
    "observed_at": "2026-09-28T10:00:00+02:00",
    "expires_at": "2026-10-02T18:00:00+02:00",
    "sources": [{
      "kind": "gmail", "id": "message-1",
      "from": "Person <person@example.org>",
      "url": "https://mail.google.com/mail/u/0/#all/message-1"
    }]
  }]
}
```

Maximal fuenf aktuell relevante, noch offene Punkte. Abgelaufene Termine und
erledigte Themen entfernen. Bestehende offene Punkte erneut gegen ihre
Quellen pruefen; keine blosse Kopie alter Meldungen. Auch ein erfolgreicher
Lauf ohne neue Punkte veroeffentlicht einen aktuellen Snapshot.
`observed_at` ist der Quellenzeitpunkt, `expires_at` die letzte sinnvolle
Anzeigezeit, nicht die Behauptung einer Erledigung.

Cody setzt den Abschnitt `Follow-up` verbindlich aus dem
geprueften Snapshot ein. Der Pruefzeitpunkt und Quellenlinks bleiben sichtbar.
Die erwartete Aktualitaet richtet sich nach dem letzten werktags faelligen
05:00-Lauf: Am Wochenende bleibt Freitag mit sichtbarem Datum nutzbar; am
Montag braucht Cody einen neuen Lauf. Verpasste Laeufe, unvollstaendige
Quellen und ungueltige Daten fuehren zu einem deutlichen Hinweis.

Mailquellen brauchen einen menschlichen Absender in `from`; Listen-/Bulk-Header
werden optional in `headers` mitgegeben. Newsletter, Werbung, Nachrichten von
Eveline und abgeschlossene Themen gehoeren nicht in den Snapshot. Bereits
beantwortete Nachrichten sind keine offenen Antwortaufgaben. Nur eine noch
ausstehende Folgeaktion rechtfertigt die weitere Nachverfolgung.

Fuer lokale Tests kann `FOLLOW_UP_SNAPSHOT_PATH` auf einen privaten Snapshot
zeigen. Der normale GitHub-Lauf verwendet ausschliesslich das Secret.

Manuelle Freigabemails verwenden den Workflow-Eingang `test_email=true`.
Ihr Betreff enthaelt `TEST`; der normale Morgenversand bleibt unveraendert.
Der Duplikatschutz gilt auch fuer Testmails. Bei einem Abendtest zeigt die
Wetterkarte die vollstaendige Prognose fuer den naechsten Tag mit Datum.
