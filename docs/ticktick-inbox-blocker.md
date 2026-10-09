# TickTick-Inbox: konkreter OpenAPI-Blocker

**Historischer Befund.** Der aktuelle Draft verwendet den offiziellen
Remote-MCP; siehe [MCP-Handoff](ticktick-mcp-handoff.md). Die nachfolgenden
OpenAPI-Befunde erklären die eingestellte Route, nicht den jetzigen
MCP-Vertrag. Alte Setup-Anweisungen gelten nicht mehr; kein weiterer Login
oder Supportversand nach dieser Untersuchung.

Stand 06.10.2026. PR #5 bleibt Draft; keine Aktivierung, kein Merge und keine
weitere Nutzer-Anmeldung als Versuch. Scope bleibt ausschließlich `tasks:read`.
Die Secret-Ablage wurde bei beiden lokalen Proben nicht erreicht.

## Was tatsächlich belegt ist

Der Nutzer-OAuth-Callback und Token-Austausch liefen bis zur Aufgabenprüfung.
Die erste Probe erreichte die Projektobjektprüfung: Die Antwort war ein Objekt
mit Aufgabenarray, aber `project` war fehlend oder null. Weder Anzahl noch Inhalte
dieses tatsächlichen OAuth-Aufgabenarrays wurden aufgezeichnet.

Der separate MCP-Connector zeigte für seine virtuelle Inbox `project: null` und
ein leeres Aufgabenarray. Das ist ein Connector-Befund und kein Vertrag für den
direkten OpenAPI-Aufruf im GitHub-Runner. Die Übertragung dieser Annahme war falsch.

Der Formatfix e3d2916 verlangte anschließend eine frische gültige virtuelle ID
`inbox` in einem zusätzlichen unpaginierten OpenAPI-Listenabruf. Auch diese Probe
scheiterte. Der damalige Fehler unterscheidet nicht zwischen fehlendem Treffer,
doppeltem Treffer oder ungültigem Status/Typ. Deshalb wird nicht nachträglich
behauptet, der nicht gespeicherte Antwortkörper habe eine bestimmte Form gehabt.
Belegt ist: Es gab keine vom Adapter bestätigte vollständige Inbox-Abdeckung.

Der spekulative Zusatzabruf ist entfernt. Fehlende Inbox-Projektidentität bleibt
ein sichtbarer Fehler; alle zuvor gelesenen Aufgaben werden verworfen. Nur
fest definierte Datentypen, Feldvorhandensein und Anzahl dürfen in zukünftigen
Strukturdiagnosen erscheinen. Keine Aufgaben, IDs, Kontodaten, Tokens, freien
Antwortschlüssel oder Inhalte werden ausgegeben oder gespeichert.

## Offizieller API-Vertrag und Discovery

Quelle: [TickTick Open API](https://developer.ticktick.com/docs/openapi.md),
am 06.10.2026 frisch ohne Authentifizierung abgerufen. Dokument-Hash:
`01fba2c6a4281268901373d4e33ca4775a38f4ac04ba2bc4bcea10bb64d24727`.

| Dokumentierter Weg | Für die Anforderung relevant | Offene Grenze |
| --- | --- | --- |
| `GET /open/v1/project`, auch mit offset/limit | Reguläre zugängliche Listen und ihre IDs | Keine Zusage einer virtuellen Inbox oder Inbox-ID-Discovery im veröffentlichten Vertrag |
| `GET /open/v1/project/{projectId}/data` | Aufgabenarray zu einer Projekt-ID | Kein dokumentierter `inbox`-Alias für diesen GET; leer/null beweist keinen gültigen Inbox-Abruf |
| `POST /open/v1/preference` | Benutzerzeitzone | Keine dokumentierte Benutzer-/Inbox-ID als Discovery-Feld |
| `POST /open/v1/task/undone` | Hier ist `inbox` ausdrücklich als Alias dokumentiert | startDate/endDate sind erforderlich; maximal 14 Tage. Kein vollständiger Vertrag für undatierte und beliebig alte offene Aufgaben |
| `POST /open/v1/task/filter` | Status- und Projektfilter, optionale Datumsfilter | Maximal 200 Aufgaben; keine dokumentierte Pagination und keine gesonderte Inbox-Alias-Zusage |
| `POST /open/v1/task/search` | Optionale Such-/Projekt-/Status-/Datumsfelder | Vollständige Enumeration ohne Suchwort, Inbox-Alias und Pagination/Ergebnisgrenzen sind nicht eindeutig zugesichert |

**Schlussfolgerung:** Für die geforderte vollständige Inbox einschließlich
undatierter Aufgaben ist derzeit kein dokumentierter und live bestätigter
`tasks:read`-Leseweg vorhanden. Das ist ein API-/Discovery-Blocker des gewählten
Runners. Es ist keine Behauptung, dass die Open API überhaupt keine Inbox-Abfrage
kennt: Der datumsgebundene Weg ist dokumentiert, genügt aber nicht allein.
Andere Kandidaten bleiben offen bis zu einer belastbaren Herstellerklärung.

Eine tatsächliche unterstützte Inbox-ID lässt sich aus den geprüften offiziellen
Discovery-Endpunkten nicht ableiten. Keine ID nach Namensmuster erraten, keine
Token-/JWT-Daten auslesen und keine Probeaufgabe anlegen, verschieben oder löschen.
Undokumentierte interne Web-APIs und Browser-Session-Cookies werden nicht genutzt.

## Sichere nächste Wege

1. **Hersteller bestätigt den vollständigen OpenAPI-Vertrag.** Der vorbereitete
   [technische Anfragetext](ticktick-support-draft.md) verlangt einen dokumentierten
   Weg für sämtliche offenen Inbox-Aufgaben samt undatierten Einträgen, Discovery,
   Pagination und ausschließlich `tasks:read`. Er enthält keine Geheimnisse und
   wurde nicht versendet. Erst mit belastbarer Antwort wird der Adapter gegen
   passende Fixtures geprüft; danach ist eine gezielte persönliche Autorisierung
   und ein vollständiger Mail-freier Actions-Livecheck sinnvoll.
2. **Cody in einem Connector-berechtigten Runner ausführen.** Eine Ausführung
   direkt in der bereits berechtigten Cloud-Umgebung könnte TickTick über deren
   verwalteten Connector lesen. Das erfordert einen konkreten neuen Runner- und
   Versandentwurf sowie eine separate Freigabe vor Einrichtung. Die bestehenden
   ChatGPT-MCP-Zugangsdaten werden auch dafür weder ausgelesen noch nach GitHub
   übertragen. Die heutige Verbindung allein belegt noch keinen automatisierten
   vollständigen Cody-Lauf.

Inbox-Auslassung, eine angeblich leere Inbox, alleinige Datums-/Kalenderfeeds,
manuelle Aufgabenverschiebungen oder alte Apple-Snapshots erfüllen die aktuelle
Anforderung nicht und sind keine automatische Ausweichlösung. Der bestehende
Apple-/Wiki-LaunchAgent und die produktive Hauptbranch bleiben unverändert.

## Einrichtungssperre und Prüfungen

`scripts/setup_ticktick_oauth.py` stoppt mit `SETUP_PAUSED_FOR_INBOX=True` bereits
vor versteckter Eingabe, Browser-OAuth und GitHub-Zugriffen. Die Sperre lässt sich
nicht per CLI-Argument oder Umgebungsvariable umgehen. Tests dürfen sie nur mit
synthetischen Werten patchen, um den unabhängig geprüften OAuth-Ablauf zu erhalten.
Sie wird nicht allein aufgrund grüner Unit-Tests oder MCP-Metadaten aufgehoben.

Regressionstests sichern: null/fehlende Inbox-Identität wird nie als leer/frisch
ausgegeben; virtuelle Listenmetadaten lösen keinen spekulativen Zusatzabruf aus;
Teilergebnisse werden verworfen; Strukturdiagnostik enthält keine privaten Werte;
die Einrichtungssperre verhindert sämtliche Auth-/Secret-Schritte. Die bestehende
Termin-, Status-, Pagination-, Fehler- und Wetterprüfung bleibt erhalten.
