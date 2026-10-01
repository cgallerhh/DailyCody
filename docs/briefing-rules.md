# Verbindliche Briefing-Regeln

Das Briefing wird standardmaessig aus Quelldaten aufgebaut
(`CODY_GENERATION_MODE=source`). Freie KI-Texte, Tagesbewertungen, Humor,
Metaphern und erfundene Antwortversprechen gehoeren nicht hinein.
Das konfigurierte Morgen-Zitat bleibt eine getrennte, belegte Ausnahme.

## Persoenliche Mail

Nur menschliche Absender. Newsletter, Werbung, Massenmail-Header,
automatische Absender und Nachrichten von oder an Eveline sind ausgeschlossen.
Gmail-Kategorien allein reichen nicht als Filter. Ein ungelesener Status ist
keine offene Aufgabe.

Der aktuelle eigene Nachrichtentext wird vor Signatur und zitiertem Verlauf
abgetrennt. Vor jeder Antwortaufgabe wird geprueft, ob bereits eine eigene
Antwort existiert. Nachfasspunkte erfordern eine echte offene Rueckfrage,
keine Terminbestaetigung, blosse Information oder zitierte alte Frage.
Explizite Fragen fuer eine spaetere Besprechung sind keine offenen Antworten.
Eine eigene Zusage wird dadurch nicht automatisch zu einer neuen Erinnerung;
eine unabhaengige echte Rueckfrage in derselben Mail bleibt erhalten.
Versand-/Bestellbestaetigungen sind auch ohne Massenmail-Header automatisch.
Rueckgabehinweise wie "kannst du zuruecksenden" sind keine Antwortaufforderung.
Eine Absage oder als beendet markierter Vorgang verschwindet aus allen
Aktions- und Nachfassabschnitten. Explizite Nutzerentscheidungen liegen in
`data/resolved_topics.json` und haben Vorrang vor alten Mailformulierungen.

Alle faelligen und ueberfaelligen Apple-Erinnerungen werden angezeigt,
nicht nur die ersten acht. Fehlende oder veraltete Quelldaten bleiben sichtbar.

## Lieferungen

Automatische Bestell- und Carrier-Mails sind hier notwendige Fachquellen,
aber keine persoenlichen Mail-Aufgaben. Eine private Aussage wie
"Wurst bestellt", ein Autoangebot oder ein Digital-Abonnement ist kein Paket.

Bei vertrauenswuerdigen Transaktionsabsendern wird auch der Papierkorb gelesen:
Das Loeschen einer Mail storniert weder Bestellung noch Lieferung. Werbung
wird trotzdem ausgeschlossen. Bei BestSecret ist der HTML-Teil massgeblich,
wenn der Textteil nur Footer und Links enthaelt. CSS, unsichtbare Preheader
und zitierte Verlaeufe werden nicht als Geschaeftsinhalt interpretiert.

Bestellt und versendet sind verschiedene Zustaende. Eine Bestaetigung mit
"wird vorbereitet" ist kein Versand. Verstrichene ETA oder alte Versandmails
beweisen keine Zustellung: Sie werden als ungeklart angezeigt, nicht still
unterdrueckt. Abschlussmeldungen brauchen passende Bestell-/Sendungsdaten;
eine gelieferte BestSecret-Sendung darf keine andere Bestellung schliessen.
Eine bestaetigte Stornierung der gesamten Bestellung schliesst nur aeltere
Status derselben konkreten Bestellung. Teilstornierungen, Anfragen und
fehlgeschlagene Stornierungen sind kein Gesamtabschluss. Relative Angaben
wie "morgen" werden an das Maildatum in der konfigurierten Zeitzone gebunden
und als Datum ausgegeben; ausdrueckliche Zeitspannen bleiben Zeitspannen.

Alle Suchseiten werden gelesen und dedupliziert. Wiederholte Pagination-Token
oder das Sicherheitslimit fuehren zu einem Fehler, nicht zu falschen Leerlisten.
Der Suchhorizont betraegt 60 Tage; dauerhaft geloeschte Mails sind nicht lesbar.

## Follow-up

Ein gueltiger privater Monitor-Snapshot hat Vorrang. Er braucht Zeitstempel,
vollstaendige Quellenpruefung, Quellenlinks und menschliche Mailabsender.
Ohne Snapshot zeigt Cody relevante persoenliche Mail-Rueckmeldungen live,
mit sichtbarer Kennzeichnung "Gmail live". Das ist kein behaupteter
erfolgreicher Monitorlauf und deckt keine Outlook-Quelle ab.

Quellen werden vor der Ausgabe dedupliziert. Bereits beantwortete Vorgange
duerfen als Information erscheinen, nicht als erneute Antwortaufgabe.
Die private GitHub-Uebergabe des separaten Monitors bleibt zustimmungspflichtig.

## Pruefung

`python3 -m unittest discover -s tests`

Regressionen muessen insbesondere Papierkorb-Bestellungen, HTML-only Details,
Newsletter unter CATEGORY_UPDATES, Eveline, abgeschlossene Autoanfragen,
zitierte Fragen, beantwortete Threads, fehlende Monitor-Snapshots und
ungeklaerte alte Lieferstatus abdecken. Ein gruener Unit-Test ersetzt keine
Pruefung mit echten Quelldaten. Private Replay-Daten niemals committen.
