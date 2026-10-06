# Konkreter MCP-Handoff – noch gesperrt

Stand 06.10.2026. Dies ist der **abzustimmende Ablauf**, keine Aktivierung.
`scripts/setup_ticktick_mcp.py` beendet sich mit `HANDOFF_APPROVED = False`
vor jedem Login, Registrierungs-, Keychain- oder Secret-Aufruf. Der alte
OpenAPI-Setup-Helfer bleibt gesperrt. Keine interaktive Browseranmeldung vor
ausdrücklicher Abstimmung und keine weitere Anmeldung als Versuch.

## Was der vorhandene Runner autonom kann

Das Briefing bleibt auf GitHub Actions mit dem bisherigen Berliner Zeitplan.
Mit einem gültigen Access-Snapshot kann es bei jedem Lauf Aufgaben frisch vom
Remote-MCP lesen. **Dieser Entwurf erneuert das Actions-Secret nicht selbst.**
Der Snapshot enthält bewusst keinen Refresh-Token und läuft ab.

Der vorbereitete Refresh-Pfad verwendet einen eigenen macOS-Keychain-Eintrag
als dauerhaften Zustand. Für eine spätere lokale Erneuerung und Übertragung an
GitHub müsste dieser Mac zu den Erneuerungszeiten eingeschaltet, vernetzt und
der eigene Keychain-Eintrag zugänglich sein. Ein ständig laufender Prozess ist
nicht erforderlich; ein periodischer eigener LaunchAgent wäre möglich.
Bei Schlaf/Ausschalten wären Erneuerung und Veröffentlichung jedoch blockiert.
Wie oft der Mac verfügbar sein muss, hängt von der noch unbekannten echten
Tokenlaufzeit ab. Ablauf führt im Briefing sichtbar zu unbekanntem Aufgabenstand.

**Es ist noch kein solcher Tokenkeeper, LaunchAgent oder neuer GitHub-Zugriff
eingerichtet.** Der vorhandene Apple-/Wiki-LaunchAgent bleibt aktiv. Für einen
vollständig autonomen GitHub-Betrieb wäre stattdessen ein abgestimmter Cloud-
Keeper mit verschlüsseltem beschreibbarem OAuth-Zustand und einem sicheren
Übergabeweg nötig. Das ist zusätzliche Architektur und zusätzliche dauerhafte
Berechtigung; sie wird nicht durch die bisherigen ChatGPT-Tokens hergestellt.

Vor Einrichtung ist zu entscheiden, ob die Mac-Abhängigkeit akzeptabel ist.
Bis dahin dient der vorbereitete Keychain-Pfad nur als konkret reviewbarer
Handoff-/Lifecycle-Entwurf. Keine Produktionsaktivierung aufgrund von Unit-Tests.

## Öffentliche Befunde und noch fehlender Live-Beleg

Der [offizielle TickTick-MCP-Leitfaden](https://help.ticktick.com/articles/7438129581631995904)
nennt `https://mcp.ticktick.com`, Streamable HTTP, OAuth/Bearer und Refresh
nach Neustart. Die öffentliche
[Resource-Discovery](https://mcp.ticktick.com/.well-known/oauth-protected-resource)
bindet `https://mcp.ticktick.com/`, Autorisierung über `https://ticktick.com/`
und `tasks:read`. Die
[Authorization-Discovery](https://ticktick.com/.well-known/oauth-authorization-server)
nennt Public-Client-Auth `none`, PKCE S256 und dynamische Registrierung, aber
nur den Grant `authorization_code`. Refresh ist deshalb **weder widerlegt
noch live bestätigt**. Der unauthentifizierte native Initialize-Aufruf erhielt
HTTP 401; dessen echte `tools/list`-Schemas wurden noch nicht abgerufen.

Der lesende verbundene Connector fand keine aktive undatierte Inbox-Aufgabe.
Die geforderte Abnahme braucht eine **vorher bekannte aktive undatierte Inbox-ID**.
Ein leeres Array genügt nicht. Falls keine vorhandene Aufgabe verfügbar ist,
muss Christian zuerst ausdrücklich eine menschlich angelegte Probe erlauben.
Kein Helfer erzeugt, verschiebt, datiert oder erledigt Aufgaben.

## Sicherer Ablauf nach Abstimmung

1. Mac- oder Cloud-Zustandsarchitektur abstimmen und eine vorhandene bekannte
   Inbox-Probe bestimmen. Keine neue Anmeldung, solange einer dieser Punkte fehlt.
2. Den geprüften Swift-Helfer aus `scripts/ticktick_mcp_keychain.swift` lokal
   unter `.build/ticktick-mcp-keychain` mit privaten Dateirechten kompilieren.
   Der Helfer adressiert ausschließlich Service
   `com.dailycody.ticktick-mcp.oauth.v1`, Account `daily-cody-read-only`.
   Er enumeriert keine Keychain und liest keine fremden OAuth-/MCP-Einträge.
   Die eigene neue verschlüsselte Ablage und ihre spätere Nutzung sind
   Bestandteil dieses neu abzustimmenden Handoffs.
3. Erst nach Abstimmung die Code-Sperre kontrolliert lösen. Christian startet
   den Helfer im eigenen Terminal; die bekannte Probe-ID ist nicht geheim:

   ```bash
   # Erst nach Abstimmung und Entsperren; keine Tokens in diese Befehle einsetzen.
   export TICKTICK_MCP_INBOX_PROBE_ID='ID_DER_BEKANNTEN_UNDATIERTEN_INBOX_AUFGABE'
   python3 scripts/setup_ticktick_mcp.py
   ```

4. Der Helfer prüft zunächst die vorhandene GitHub-CLI-Berechtigung für genau
   `cgallerhh/DailyCody`; kein neuer GitHub-Token und kein Scope-Upgrade.
   Fehlt der eigene neue Keychain-Eintrag, prüft er die öffentliche Discovery
   und fordert eine eigene Public-Client-Registrierung mit ausschließlich
   `tasks:read`, Callback `http://127.0.0.1:8766/callback` und Code/Refresh an.
   Bestätigt der Server Public-Client, Callback, Scope und Refresh-Grant nicht,
   endet der Ablauf **vor dem Browser**. Kein automatischer zweiter Versuch
   mit einer anderen App, Auth-Methode oder breiterem Scope.
5. Bei bestätigter Registrierung öffnet der menschliche Helfer einmal OAuth
   mit PKCE S256, CSRF-`state` und festem MCP-Resource-Parameter. Christian
   prüft selbst `christian.galler@gmail.com` und `tasks:read`. Der Callback ist
   nur an localhost gebunden; der Code wird ohne Ausgabe direkt gegen Tokens
   ausgetauscht. Keine Tokenwerte, Browser-Cookies, Client-Secrets oder Codes
   in Chat, History, Prozessargumenten, Git, Testberichten oder Logs.
6. Scope, positiver bestätigter Ablauf und Refresh-Token müssen gültig sein.
   Dann wird **vor der Aufgabenprobe** der eigene Seed verschlüsselt im
   abgestimmten Keychain-Eintrag gespeichert. So kann ein Schema-/Probe-Fehler
   mit dem eigenen Zustand geprüft werden, ohne eine neue Anmeldung zu erzwingen.
   Das ist eine konkrete Speicheränderung und wird erst nach Zustimmung ausgeführt.
7. Mit dem eigenen Zustand erfolgen nacheinander: tatsächlicher Remote-Katalog
   und Schemaprüfung, alle aktiven Listen inklusive bekannter undatierter Inbox-ID,
   erzwungener Refresh, Speichern des rotierten Zustands, erneutes Laden durch
   unabhängigen nativen Helferprozess und erneuter vollständiger Abruf mit
   erwarteter Generation. Bei Fehler bleiben Aufgaben unbekannt; keine Mail,
   kein neues Login und keine Secret-Veröffentlichung als vermeintlicher Erfolg.
8. Erst bei drei erfolgreichen lokalen Prüfungen würde `gh secret set` den
   **Access-Snapshot ohne Refresh-Token** als `TICKTICK_MCP_AUTH_JSON` und die
   Probe-ID als `TICKTICK_MCP_INBOX_PROBE_ID` per privater stdin-Pipe an das
   exakt geprüfte Repository übertragen. Der Ablauf ist nicht atomar über zwei
   GitHub-Secrets; bei einem Publikationsfehler ist der Stand separat zu prüfen.
   Der Bericht außerhalb des Repositories enthält nur Anzahlen, Zeit, Ablauf,
   Generation und Prüfstatus. Kein Token oder Aufgabeninhalt.
9. Auf einem frischen GitHub-Runner ausschließlich den Review-Branch mit
   `ticktick_check_only=true` und `ticktick_expected_generation` aus dem lokalen
   Prüfbericht prüfen: native Remote-Schemas, alle Listen,
   bekannte undatierte Inbox-ID und aktuelle Token-Generation. Dieser Pfad
   beendet sich vor Briefing-/Mailcode. Weitere Runner-Neustartprobe muss den
   aktualisierten Snapshot laden; keine Mail als Nachweis verwenden.
10. Erst bei bestätigter echter Laufzeit und Refresh/Persistenz/Neustart die
    separat abgestimmte Erneuerung installieren und testen. Ein lokaler Keeper
    könnte denselben eigenen Keychain-Eintrag und bestehende GitHub-CLI-Auth
    benutzen; der LaunchAgent dafür ist noch zu implementieren. Schlaf-/Nacht-
    verfügbarkeit und Übergabe vor 06:00 Berlin gehören zur Abnahme.
    Erst nach vollständig erfolgreicher Abnahme gilt eine bedingte Merge-Freigabe.
    Kein Merge, Deployment oder zusätzliche Briefing-Mail davor.

## Abnahmebelege

Die Unit-Tests prüfen Transport, Schemas, Inbox/Pagination, erledigte Einträge,
Berlin-Termine, Scope/Ablauf, Refresh-Rotation, Persistenzfehler, Neustart und
Einrichtungssperre mit synthetischen Daten. Sie sind kein Live-Auth-Nachweis.

`scripts/check_ticktick_mcp_lifecycle.py` kann gegen den eigenen dauerhaften
Speicher alle lokalen Phasen prüfen. Die normale CLI nutzt einen unveränderlichen
Umgebungssnapshot; dort scheitert `--force-refresh` absichtlich vor dem Netzwerk.
Ein entsprechender grüner Snapshot-Test beweist nur einen frischen Lesezugriff,
keinen autonomen oder persistenten Refresh.

Offen bleiben: tatsächliche native Schemas, bekannte undatierte Inbox-Probe,
separate Autorisierung, bestätigter Ablauf/Refresh, echte verschlüsselte Ablage,
frische Actions-Läufe und eine abgestimmte zuverlässige Erneuerungsarchitektur.
