# TickTick-Aufgaben in Daily Cody

## Abruf und Ausgabe

Der GitHub-Runner liest bei jedem tatsächlich gebauten Briefing TickTick direkt
über die offizielle Open API. Nach dem Duplikatschutz und den übrigen Quellen
werden die Aufgaben unmittelbar vor der Zusammenstellung neu abgerufen.
Es gibt weder einen gespeicherten TickTick-Snapshot noch einen Apple-Fallback.
Die alten Apple-Reader bleiben nur als historische, getestete Hilfsfunktionen;
`main()` verwendet und aktualisiert sie nicht mehr.

- Alle aktiven Aufgabenlisten werden über `GET /open/v1/project` mit
  `offset`/`limit` vollständig ermittelt. Geschlossene Listen und Notizlisten
  sind keine aktiven Aufgabenlisten. Geteilte zugängliche Aufgabenlisten sind
  eingeschlossen.
- `GET /open/v1/project/{id}/data` liefert die vollständige offene Aufgabenliste.
  Zusätzlich wird **immer** `GET /open/v1/project/inbox/data` gelesen, auch wenn
  die paginierte Listenübersicht die virtuelle Inbox auslässt. Keine feste
  Listen-ID-Konfiguration, keine Titel-Deduplizierung, keine 8-/12-Aufgaben-Grenze.
- Der alternative Filter-Endpunkt hat laut Dokumentation eine Grenze von 200
  Aufgaben und wird deshalb nicht verwendet. Der Listeninhalt-Endpunkt hat
  keine dokumentierte Pagination. Unerwartete Fortsetzungsmarker werden als
  unvollständige Abdeckung abgelehnt.
- Stabile TickTick-Aufgaben-IDs deduplizieren dieselbe Aufgabe. Zwei Aufgaben
  mit demselben Titel bleiben zwei Aufgaben. Widersprüchliche Duplikate während
  eines Abrufs führen zu einer sichtbaren Fehlermeldung.
- Status `0` ist offen; `2` (erledigt), `-1` (aufgegeben) und Notizen entfallen.
  Ein nach Wiederöffnung/Wiederholung verbliebenes `completedTime` ersetzt
  nicht den aktuellen Status. Wiederholungen werden nicht lokal neu berechnet.
- Fällige und überfällige Aufgaben erscheinen unter `Today's to-dos`.
  Der Ausblick unter `Reminders` umfasst heute plus zwei Tage, freitags sieben
  Tage, jeweils einschließlich der Grenze. Alle undatierten Aufgaben bleiben
  dort sichtbar mit **ohne Termin**. Warten-auf-Aufgaben aus Listennamen, Tags
  oder ausdrücklichen Textmarkern erscheinen unter `Waiting for...`, auch ohne
  Termin oder mit späterem Termin. Es wird keine Nachfassfrist erfunden.
- Ausschließlich `dueDate` wird als **fällig** bezeichnet. Ein alleiniger
  `startDate` wird als **geplant** ausgegeben. Termine enthalten das Jahr;
  Datumsauswahl arbeitet auf ISO-Daten, nicht auf kurzen Anzeigeetiketten.
  Zeitpunkte werden nach Europe/Berlin umgerechnet. Ganztägige Aufgaben
  behalten das Kalenderdatum ihrer Aufgabenzeitzone und erhalten keine Uhrzeit.
  Mehrdeutige Zeitpunkte ohne Offset in einer Sommerzeitwechsel-Stunde werden
  abgelehnt. TickTicks dokumentiertes Datumformat enthält einen Offset.
- Aufgaben werden ausschließlich aus diesem Abruf übernommen. Der alte
  Bewerbungs-Wiki-Snapshot ergänzt und unterdrückt keine TickTick-Aufgaben.
  Die unabhängigen Gmail-/Monitor-Abschnitte bleiben ihren vorhandenen
  Quellenregeln unterworfen. Wetterbox und Mail-HTML bleiben unverändert.

Ein erfolgreicher Abruf bekommt einen sichtbaren Prüfzeitpunkt mit Inbox-Hinweis.
Bei fehlendem Token, HTTP 401/403, Netzwerkfehler, Zeitlimit, ungültigen Daten
oder unvollständiger Pagination wird der komplette Aufgabenabruf verworfen.
Die Mail nennt **TickTick-Aufgabenstand unbekannt**; sie behauptet dann keine
leere aktuelle Aufgabenliste. Andere Quellen können weiterhin erscheinen.
Fehlertexte enthalten keine Tokens, Aufgabeninhalte oder API-Antworttexte.
Keine Redirects mit Authorization-Header. Requests sind ausschließlich GET,
mit 15 Sekunden je Request, 90 Sekunden Abrufbudget und höchstens drei
Versuchen bei HTTP 429/5xx oder Transportfehlern. Kein persistenter Cache.

## Authentifizierung: erforderliche Freigabe

Am 06.10.2026 wurde die separate Read-only-Einrichtung, sichere Secret-Ablage,
Mail-freie Live-Prüfung und der Merge **nach erfolgreicher Prüfung** ausdrücklich
freigegeben. Die geheime Eingabe/Autorisierung ist ein notwendiger Nutzer-Handoff.
Konkrete Schritte: [sichere OAuth-Nutzerübergabe](ticktick-oauth-handoff.md).

Die ChatGPT-/dot-TickTick-Verbindung authentifiziert den GitHub-Runner nicht.
Es wurden keine MCP-Zugangsdaten gelesen, kopiert oder gespeichert.
Der Workflow erwartet einen **separaten** Actions-Secret `TICKTICK_ACCESS_TOKEN`.
Diese Änderung legt das Secret nicht an.
Ein Audit ausschließlich der Secretnamen fand noch kein `TICKTICK*`-Secret
im Repository. Es wurden keine Secretwerte abgefragt.

Vor Einrichtung ist konkret freizugeben:

1. Eine eigene TickTick-OAuth-App für Daily Cody registrieren bzw. eine bereits
   hierfür genehmigte App verwenden. Christian autorisiert das Konto selbst,
   ausschließlich mit **`tasks:read`**. Kein `tasks:write`.
2. Den OAuth-Code über die registrierte Redirect-URI und einen geprüften
   `state` austauschen. Client-Secret und Token dürfen ausschließlich im
   vertrauenswürdigen lokalen Prozess bzw. in verschlüsselter Secret-Verwaltung
   vorkommen, niemals in Chat, Git, Shell-History, Prozessargumenten oder Logs.
3. Den neuen OAuth-Access-Token nur als verschlüsseltes GitHub-Actions-Secret
   `TICKTICK_ACCESS_TOKEN` in `cgallerhh/DailyCody` speichern, z.B. mittels
   `gh secret set ...` über stdin. App-Client-Secret wird vom Runner nicht benötigt.
   Der Token gewährt dem GitHub-Runner dauerhaften Lesezugriff bis Ablauf/Widerruf.
   Die tatsächliche Laufzeit ist beim genehmigten OAuth-Austausch festzuhalten.

Die offizielle Dokumentation nennt derzeit nur `authorization_code` als Grant,
keinen zugesicherten Refresh-Token-Vertrag. Deshalb gibt es keinen erfundenen
automatischen Refresh. Widerruf/Ablauf ist sichtbar und erfordert erneute
Autorisierung. Die neue persönliche API-Token-Option des Anbieters wird wegen
nicht dokumentiertem minimalem Read-only-Scope nicht automatisch eingerichtet.

Referenz: [offizielle TickTick Open API](https://developer.ticktick.com/docs/openapi.md),
am 06.10.2026 geprüft: OAuth, Projekt-Pagination, Filter-Grenze, Status und Datumfelder.

## Sichere Live-Abnahme vor Produktivumschaltung

Nach Auth-Freigabe läuft zunächst ausschließlich die Aufgabenprüfung:

```bash
# Token sicher in der Prozessumgebung bereitstellen, keinen Wert hier einsetzen.
python3 scripts/check_ticktick_access.py
```

Oder auf dem tatsächlichen GitHub-Runner den bestehenden Workflow auf dem
Review-Branch mit `ticktick_check_only=true` dispatchen. Dieser Pfad führt
ausschließlich das Prüfskript aus und beendet sich vor dem Briefing-Code.
Er benötigt keine Google-Abfragen und baut oder sendet keine Mail.
Logs zeigen nur Anzahl relevanter offener Aufgaben, Listenabdeckung inklusive
Inbox und Abrufzeit; keine Titel oder Tokens. Bei Fehler endet er mit Exit 1.

**Inbox mit dem eigenen `tasks:read`-OAuth-Token ist noch nicht live verifiziert.**
Die Dokumentation nennt `inbox` als Projekt-ID für offene Aufgaben und erlaubt
den projektweisen GET-Abruf mit Bearer-Token; sie zeigt kein gesondertes
Inbox-/Scope-Beispiel für diesen GET-Endpunkt. Das ist eine verbleibende
Abnahmebedingung, kein durch den MCP-Test bewiesener API-Zugriff. Der Adapter
verwirft alle Aufgaben, wenn der direkte Inbox-Abruf 403/404 liefert.

Die Abnahme muss alle aktiven Listen, auch leere Inbox, gegen den lesenden
TickTick-Connector vergleichen und Termine/Ganztagsdaten bestätigen.
Der separate Connector-Gegencheck in dieser Arbeit ist keine erfolgreiche
Authentifizierung der Open API im Actions-Runner.
Erst danach gilt die erteilte bedingte Freigabe für Merge und produktive Umschaltung.
Keine zusätzliche Briefing-Mail als Test versenden.

## Tatsächliche Runner und Zeitpläne: Audit 06.10.2026

| Zweck | Tatsächlicher Runner / Zeitplan | Behandlung |
| --- | --- | --- |
| Morgenbriefing | GitHub Actions, ubuntu-latest, Python 3.12; externer `workflow_dispatch` um 06:00 Berlin, Backup `*/5 4-7 * * *` UTC; Versandfenster 06:00–08:59 Berlin mit Duplikatschutz | Zeitpunkt und Versandregeln bleiben erhalten; Aufgaben jetzt live aus TickTick |
| Apple-/Wiki-Export | geladener Mac-LaunchAgent `com.dailycody.reminders-export`; alle 1800 s, RunAtLoad; Arbeitsordner `~/Library/Application Support/DailyCody/repo`; Runner `export_apple_reminders_if_window.sh` | Noch unverändert, keine produktive Umschaltung während Review |
| Lokale Codex-Automationen | Keine passende DailyCody-/Apple-Export-Automation in `~/.codex/automations` gefunden | Keine pauschale Änderung |
| Benutzer-Crontab | Keine passende DailyCody-/Apple-Export-Zeile gefunden | Keine Änderung |

Die Hauptbasis war `f992b67c9dc9842dd1f2bc3157c243f5c3d924e8`.
PR4/Wetterfix `38c0f8975cff03f75c4d172468740d883db24163` ist ein Vorfahr.
Letzter geprüfter erfolgreicher Morgen-Dispatch:
[05.10.2026, 06:00 Berlin](https://github.com/cgallerhh/DailyCody/actions/runs/37261698807).
Der externe Scheduler selbst ist nicht zugänglich; der Dispatch ist belegt.

Der LaunchAgent exportiert im Fenster 23:59–06:59 sowie bei über einer Stunde
alten Daten/ausstehenden Pushes. Sein Exportskript veröffentlicht zusätzlich
`application_wiki_snapshot.json`. Deshalb ist ein pauschales Stoppen unzulässig:
Nach erfolgreicher Abnahme und Produktivfreigabe prüfen, ob andere Verbraucher
den Wiki-Export brauchen; gegebenenfalls separat erhalten. Cody liest diesen
Snapshot nach der Umstellung nicht mehr. Anschließend ausschließlich den
genannten Apple-LaunchAgent gezielt entladen/archivieren, mit gesicherter
Plist für eine reversible Wiederherstellung. Keine Änderungen an Aufgaben.
Der Workflow erwartet keinen Apple-Refresh und keinen frischen Apple-Export mehr.

## Verifikation

```bash
python3 -m unittest discover -s tests
python3 -m py_compile src/*.py scripts/check_ticktick_access.py
git diff --check
```

Neue Tests verwenden ausschließlich synthetische Daten. Die Tests prüfen
Listen-Pagination, Inbox, mehr als 200 Aufgaben, stabile IDs, abgeschlossene
und wiedergeöffnete Aufgaben, Teilausfälle, Authfehler, Retry-/Zeitgrenzen,
Mitternacht/Jahreswechsel/Sommerzeit, undatierte und Warten-auf-Aufgaben,
fehlende Runner-Authentifizierung und den Hauptpfad ohne Apple-Fallback.
Die separate Test-Workflow prüft Python 3.12 ohne Secrets und ohne Mailversand.
