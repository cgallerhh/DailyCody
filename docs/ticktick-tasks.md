# TickTick-Aufgaben in Daily Cody

**Stand 06.10.2026: getesteter Draft, noch nicht aktiviert.** Der Produktionspfad
dieses Branches nutzt den offiziellen Remote-MCP. Die tatsächlichen
authentifizierten Tool-Schemas, vollständige Inbox-Abdeckung, Refresh und ein
frischer GitHub-Runner sind noch nicht live abgenommen. Die Einrichtung bleibt
vor Registrierung, Browseranmeldung und Speicheränderung gesperrt.
[Konkreter Handoff und Runner-Abhängigkeit](ticktick-mcp-handoff.md).
Die frühere OpenAPI-Inbox-Untersuchung ist [historisch](ticktick-inbox-blocker.md).

## Abruf und Ausgabe

Nach Duplikatschutz und den übrigen Quellen ruft `main()` Aufgaben unmittelbar
vor der Zusammenstellung frisch ab. Es gibt keinen gespeicherten Aufgabenstand
und keinen Apple-Fallback. Der alte Bewerbungs-Wiki-Snapshot ergänzt oder
unterdrückt keine TickTick-Aufgaben. Historische Apple-Hilfsfunktionen bleiben
getestet; der alte OpenAPI-Reader liegt ausschließlich als Testreferenz vor.

- Streamable HTTP verwendet JSON-RPC-POST an `https://mcp.ticktick.com/`.
  Jede neue Session initialisiert das Protokoll und liest den paginierten
  `tools/list`-Katalog. Erlaubt sind ausschließlich **`list_projects`** und
  **`get_project_with_undone_tasks`**. Keine Schreibtools, Sampling-Aufrufe,
  Cookies, Redirects oder alte SSE-Transportalternative.
- Die remote gelieferten Eingabe-/Ausgabe-Schemas werden vor bzw. nach jedem
  Tool-Aufruf geprüft. Unbekannte Schema-Anforderungen, andere Toolnamen,
  notwendige Datumsfilter oder fehlende Lesetools führen zu unbekanntem Stand.
  Die derzeitigen Erwartungen stammen aus verfügbaren Connector-Metadaten;
  sie sind noch kein Beleg für den authentifizierten nativen Remote-Katalog.
- Ein unpaginierter Listenabruf muss die virtuelle Inbox `inbox` bestätigen.
  Anschließend werden alle Listen mit `offset`/`limit` gelesen. Geschlossene
  Listen und Notizlisten entfallen; zugängliche geteilte Aufgabenlisten zählen.
  Jede aktive Liste inklusive Inbox wird ohne Datumsfilter vollständig gelesen.
  Kein Filter-Endpunkt mit 200-Aufgaben-Limit und keine feste Listenauswahl.
- Nur beim MCP-Inbox-Aufruf darf `project: null` vorkommen, nachdem die frische
  MCP-Listenübersicht die Inbox bestätigt hat. Aufgaben-IDs und `projectId`
  bleiben erhalten; widersprüchliche Listenidentitäten oder Fortsetzungsmarker
  werden abgelehnt. Diese Ausnahme gilt nicht für die frühere Open API.
  Eine leere Inbox kann ein gültiges Ergebnis sein, beweist aber nicht die
  verlangte Live-Abnahme einer bekannten undatierten Aufgabe.
- Stabile Aufgaben-IDs deduplizieren gleiche Einträge. Gleichnamige Aufgaben
  mit verschiedenen IDs bleiben getrennt. Widersprüchliche Duplikate verwerfen
  den gesamten Abruf. Status `0` ist offen; `2`, `-1` und Notizen entfallen.
  Ein altes `completedTime` überstimmt keinen wieder offenen Status.
- Fällige und überfällige Aufgaben erscheinen unter `Today's to-dos`.
  `Reminders` umfasst heute plus zwei Tage, freitags sieben Tage, einschließlich
  der Grenze. Undatierte Aufgaben bleiben als **ohne Termin** sichtbar.
  Ausdrückliche Warten-auf-Aufgaben aus Listen, Tags oder Textmarkern erscheinen
  unter `Waiting for...`, auch ohne oder mit späterem Termin. Keine erfundenen
  Nachfassfristen und keine lokal erzeugten Wiederholungen.
- Nur `dueDate` heißt **fällig**. Ein alleiniger `startDate` heißt **geplant**.
  Zeitpunkte werden nach Europe/Berlin umgerechnet; ganztägige Aufgaben behalten
  das Kalenderdatum ihrer Aufgabenzeitzone und bekommen keine Uhrzeit.
  Termine enthalten das Jahr. Mehrdeutige lokale Zeitpunkte während der
  Sommerzeitumstellung ohne Offset werden abgelehnt.

Der erfolgreiche Abruf zeigt einen Prüfzeitpunkt mit Inbox-Hinweis. Bei Auth-,
Transport-, Schema-, Identitäts-, Pagination- oder Zeitfehlern wird der komplette
Aufgabenabruf verworfen. Die Mail sagt **TickTick-Aufgabenstand unbekannt**;
sie behauptet keine frische leere Liste und verwendet keine alten Apple-Daten.
Fehler enthalten keine Tokens, Aufgabeninhalte oder Server-Antworttexte.
Andere Quellen behalten ihre bestehenden Regeln; Mail-Layout und kompakte
14px-Wetterbox wurden nicht geändert.

Je Request gelten 15 Sekunden, insgesamt 90 Sekunden inklusive Wiederholungen
und Session-Neustart. HTTP 429/5xx und Transportfehler erhalten höchstens drei
Versuche. Eine abgelaufene MCP-Session darf einmal neu initialisiert werden;
dabei werden Teilresultate verworfen und Schemas erneut gelesen. Antwort- und
Streamgrößen sowie Pagination sind begrenzt. Kein persistenter Aufgabencache.

## Eigene Authentifizierung und Lebenszyklus

Die dot-/ChatGPT-Verbindung authentifiziert GitHub Actions nicht. Es wurden
keine Connector-Zugangsdaten gelesen, kopiert oder übernommen. Die vorbereitete
eigene Public-Client-Registrierung fordert ausschließlich `tasks:read`, PKCE
S256 und die feste MCP-Resource an. Kein Client-Secret ist vorgesehen.

Die [offizielle MCP-Anleitung](https://help.ticktick.com/articles/7438129581631995904)
beschreibt OAuth und automatischen Refresh. Die öffentlich geprüfte
[OAuth-Discovery](https://ticktick.com/.well-known/oauth-authorization-server)
advertisiert dagegen nur `authorization_code`. Das beweist weder fehlenden
Refresh noch funktionierenden Refresh. Registrierung, Tokenablauf,
erzwungener Refresh und Wiederaufnahme aus gespeichertem Zustand müssen live
bestätigt werden. Bis dahin bleibt `HANDOFF_APPROVED = False` im Setup-Helfer.

Die vorbereitete Ablage trennt zwei Rollen:

| Rolle | Vorgesehener Zustand | Grenze |
| --- | --- | --- |
| Eigener Mac-Keychain-Eintrag | Eigener Client, Access-/Refresh-Token, Ablauf und Generation | Noch nicht angelegt; nur fester Cody-Eintrag, keine Suche nach fremden Tokens |
| Actions-Secret `TICKTICK_MCP_AUTH_JSON` | Access-Snapshot ohne Refresh-Token | Funktioniert nur bis Ablauf; Actions kann ihn nicht selbst erneuern |
| Actions-Secret `TICKTICK_MCP_INBOX_PROBE_ID` | Bekannte aktive undatierte Inbox-ID | Nur Abnahme, kein dauerhaftes Kriterium für normale Briefings |

Der Refresh-Pfad prüft einen beschreibbaren dauerhaften Speicher **vor** dem
Refresh-Aufruf, speichert den neuen Zustand und lädt ihn erneut, bevor der neue
Access-Token benutzt wird. Ein Umgebungs-/Actions-Snapshot wird nicht als
dauerhafter Speicher ausgegeben und verbraucht keinen rotierenden Refresh-Token.
Ein geplanter lokaler Erneuerungsdienst ist noch nicht implementiert/installiert.
Der GitHub-Runner läuft mit diesem Entwurf nur bis zum Tokenablauf autonom.
Für Erneuerung müsste der Mac verfügbar sein; eine autonome Cloud-Lösung
benötigte eine zusätzlich abgestimmte Speicher-/Zugriffsarchitektur.

## Mail-freie Live-Abnahme

Nach abgestimmtem Handoff prüft der Helfer: bekannten offenen undatierten
Inbox-Eintrag nach ID, alle relevanten Listen, initialen Abruf, erzwungenen
Refresh, verschlüsselte Persistenz und erneutes Laden durch einen unabhängigen
Keychain-Helferprozess. Erst danach wäre eine Secret-Veröffentlichung erlaubt.
Ein weiterer Livecheck auf einem frischen Actions-Runner bleibt erforderlich.

`ticktick_check_only=true` führt ausschließlich `scripts/check_ticktick_access.py`
aus und beendet den Workflow vor Briefing-Aufbau und Mailversand. Fehlende
Probe-ID, verschwundene/erledigte/datierte Probe oder leere Inbox bestehen die
Abnahme nicht. Logs zeigen nur Anzahlen, Zeitpunkt und Prüfstatus. Der separate
Lifecycle-Harness prüft außerdem die erwartete Token-Generation. Der Actions-
Input `ticktick_expected_generation` prüft denselben Nachweis vor dem Task-Abruf;
bei einer veralteten Generation endet der Check vor dem Netzwerk. Ein Snapshot
ohne dauerhaften Speicher kann keinen Live-Refresh nachweisen.

Der lesende Connector-Gegencheck fand aktuell **keine aktive undatierte
Inbox-Aufgabe**. Es wurde keine Testaufgabe angelegt. Eine solche menschliche
Probe braucht Freigabe, falls keine vorhandene Aufgabe verwendet werden kann.
Kein Merge/Deployment oder zusätzlicher Mailtest vor vollständiger Abnahme.

## Tatsächliche Runner und Zeitpläne: Audit 06.10.2026

| Zweck | Tatsächlicher Runner / Zeitplan | Behandlung |
| --- | --- | --- |
| Morgenbriefing | GitHub Actions, Ubuntu, Python 3.12; externer Dispatch 06:00 Berlin, Backup `*/5 4-7 * * *` UTC; Versandfenster 06:00–08:59 Berlin, Duplikatschutz | Unverändert; Draft nicht aktiviert |
| Apple-/Wiki-Export | Geladener Mac-LaunchAgent `com.dailycody.reminders-export`; alle 1800 s und RunAtLoad; `~/Library/Application Support/DailyCody/repo` | Bleibt ausdrücklich aktiv; keine Umkonfiguration |
| Lokale Codex-Automationen / Benutzer-Crontab | Kein passender zusätzlicher DailyCody-/Apple-Export-Auftrag gefunden | Keine Änderung |

Basis `f992b67c9dc9842dd1f2bc3157c243f5c3d924e8` enthält den PR4-Wetterfix
`38c0f8975cff03f75c4d172468740d883db24163` als Vorfahren. Belegt ist der
[Morgen-Dispatch am 05.10.2026 um 06:00 Berlin](https://github.com/cgallerhh/DailyCody/actions/runs/37261698807).
Die Konfiguration des externen Schedulers selbst ist nicht zugänglich.
Ein erneuter Main-Abgleich vor Abschluss sah `5216cc7`; seit der geprüften Basis
hatten sich ausschließlich Apple-Export-Statuszeitpunkte geändert, kein Quellcode.
Der Mac-LaunchAgent veröffentlicht auch `application_wiki_snapshot.json`;
er wird nicht entladen. Keine Aufgaben wurden verändert oder migriert.

## Verifikation

```bash
python3 -m unittest discover -s tests
python3 -m py_compile src/*.py scripts/check_ticktick_access.py scripts/check_ticktick_mcp_lifecycle.py scripts/setup_ticktick_mcp.py scripts/setup_ticktick_oauth.py
git diff --check
```

Tests verwenden ausschließlich synthetische Daten. Sie prüfen bestehende Mail-/
Wetterregeln, Europe/Berlin-Mitternacht/Jahreswechsel/Sommerzeit, Ganztagsdaten,
Inbox, mehr als 200 Aufgaben, Pagination, stabile IDs, erledigte/wieder offene
Aufgaben, Warten-auf und undatierte Aufgaben, Teilausfälle, Auth-/Retry-/Zeitgrenzen,
JSON/SSE, Tool-Schemas, Session-Neustart, Scope-/Expiry-Grenzen, Refresh-Rotation,
Persistenzfehler, geschützte Keychain-IPC und die gesperrte Einrichtung.
Python-3.12-CI benötigt keine Secrets und versendet keine Mail. Der native
Swift-Keychain-Helfer wurde lokal nur typgeprüft, nicht gegen eine Keychain ausgeführt.
