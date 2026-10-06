# Sichere Nutzerübergabe für TickTick

**Pausiert – bitte nicht erneut anmelden.** Zwei lokale Leseproben haben den
vollständigen OpenAPI-Inbox-Zugang nicht bestätigt. Der Helfer beendet sich
jetzt vor GitHub-Abfragen, Geheimniseingabe und Browser-Autorisierung.
Keine neue App registrieren und keine Zugangsdaten erneut eingeben.
[Konkreter Befund und sichere nächste Schritte](ticktick-inbox-blocker.md).

Christian hat die separate Einrichtung mit ausschließlich `tasks:read`, die
verschlüsselte Actions-Secret-Ablage und den anschließenden Mail-freien Live-Test
freigegeben. PR #5 darf erst nach erfolgreichem Actions-Test inklusive Inbox
gemergt werden. Keine ChatGPT-MCP-Zugangsdaten verwenden.

## OAuth-Ablauf erst nach belegter vollständiger Inbox-Abdeckung

Diese Schritte dokumentieren den geprüften Ablauf; sie sind derzeit gesperrt.
Die bereits registrierte App bleibt bestehen. Die Sperre wird erst nach einer
belegten API-/Runner-Lösung und erfolgreicher Codeprüfung aufgehoben.

1. Öffne [TickTick Developer: Manage Apps](https://developer.ticktick.com/manage).
   Melde dich selbst mit deinem TickTick-Konto an. Registriere eine eigene App,
   zum Beispiel **Daily Cody (read-only)**. Bei einer Beschreibung genügt:
   `Read-only task source for my morning briefing; no task changes.`
   Im Feld **OAuth redirect URL** exakt eintragen:

   ```text
   http://127.0.0.1:8765/callback
   ```

   Wenn TickTick diese lokale URI nicht akzeptiert, abbrechen und nur diese
   Fehlermeldung ohne Geheimnisse melden. Kein anderer Redirect-Empfänger wird
   stillschweigend verwendet. Konto-/Vertragsbestätigungen selbst durchführen.

2. Öffne dein **eigenes Terminal** am Mac und führe ausschließlich diesen
   nicht geheimen Befehl aus:

   ```bash
   python3 /Users/cgaller/Documents/Codex/2026-10-06/task/DailyCody/scripts/setup_ticktick_oauth.py
   ```

   Der Helfer prüft zuerst die vorhandene lokale GitHub-Anmeldung und das feste
   Ziel `github.com/cgallerhh/DailyCody`. Anschließend verlangt er **Client-ID**
   und **Client-Secret** der gerade angelegten App in verdeckten Eingabefeldern.
   Diese Werte ausschließlich dort selbst einsetzen, nicht in die Shell-Befehlszeile,
   nicht in Chat, Screenshot, Datei, Repository oder Chat-gesteuerte Terminal-Sitzung.
   Der Helfer lehnt Echo-Fallback und umgeleitete Eingaben ab.
   Bei fehlender GitHub-Anmeldung nur den Blocker melden; keine zusätzlichen
   GitHub-Scopes oder neuen Zugangsschlüssel ohne erneute Prüfung einrichten.

3. Im automatisch geöffneten TickTick-Browser selbst autorisieren. Prüfe das
   Konto **christian.galler@gmail.com** und ausschließlich lesenden Zugriff.
   Der Helfer fordert genau **`tasks:read`** an. Wenn TickTick Schreibrechte oder
   andere Berechtigungen zeigt, abbrechen und nur den abweichenden Scope melden.
   Keine geheime Browseransicht an den Chat übermitteln.

4. Der Helfer empfängt den OAuth-Code über den bereits gebundenen lokalen
   Listener, prüft `state` und tauscht ihn direkt per TLS bei TickTick aus.
   Danach liest er alle aktiven Aufgabenlisten einschließlich Inbox direkt
   aus der Open API. Nur bei Erfolg übergibt er den Token über stdin an
   `gh secret set TICKTICK_ACCESS_TOKEN --repo github.com/cgallerhh/DailyCody --app actions`.
   GitHub CLI verschlüsselt den Wert lokal vor dem Upload. Der Token wird nicht
   ausgegeben oder in einer lokalen Datei/Umgebungsvariable abgelegt.

5. Wenn das Terminal **Erfolgreich** meldet, im Chat nur **fertig** schreiben.
   Der geheime Token muss weder kopiert noch genannt werden. Bei Fehler nur
   den redigierten Fehlertext mitteilen. Noch keine Briefing-Mail auslösen.

Bei beiden bereits beobachteten Inbox-Fehlern nach erfolgreichem Callback wurde
kein Secret gespeichert. Die beendeten Helfer haben den Token nicht aufbewahrt.
Der Callback allein bestätigt keinen vollständigen Aufgabenabruf. Keine
Zugangsdaten aus Terminal oder Prozessen nachträglich auslesen und keinen
erneuten OAuth-Durchlauf als spekulativen API-Test durchführen.

## Was danach geprüft wird

Der lokale Erfolgsbeleg `../ticktick-oauth-result.json` liegt außerhalb des
Repositories und enthält nur Scope-Name, Zählwerte, Abrufzeit, optionalen Ablauf
und einen Erfolgsstatus. Keine Tokens, Codes, Client-IDs/-Secrets oder Aufgabentexte.
Die Ausgabe ist ein lokaler Beleg; sie ersetzt die Actions-Prüfung nicht.

Danach wird PR #5 auf dem Review-Branch mit `ticktick_check_only=true` auf dem
echten GitHub-Runner geprüft. Dieser Pfad beendet sich vor jeder Briefing-Erstellung
und vor jeder Mail. Bei erfolgreicher Listen-/Inbox-Abdeckung, grüner CI und
unverändertem Scope folgt der freigegebene Merge; anschließend nochmals derselbe
Mail-freie Check auf `main`. Der normale Morgenzeitplan bleibt unverändert.
Der Apple-/Wiki-LaunchAgent wird nicht gestoppt.

## Scope und Ablauf

Wenn die Token-Antwort ein `scope`-Feld liefert, akzeptiert der Helfer nur
`tasks:read`; jede Abweichung verhindert die Secret-Ablage. Fehlt das Feld,
gilt gemäß [OAuth RFC 6749 §5.1](https://www.rfc-editor.org/rfc/rfc6749#section-5.1)
der exakt angeforderte Scope. Autorisierungs- und Token-Request enthalten beide
explizit `tasks:read`. Die Bedienperson prüft zusätzlich die Zustimmungsseite.

Eine gemeldete Laufzeit wird nur als nicht geheimes Ablaufdatum festgehalten.
Tokens mit einer gemeldeten Laufzeit unter zwei Tagen werden nicht eingerichtet,
da die TickTick-Dokumentation keinen verlässlichen Refresh-Grant zusichert und
ein solcher Token keinen unbeaufsichtigten Tagesbetrieb trägt. Fehlende
Ablaufangaben werden nicht durch eine angenommene Gültigkeit ersetzt.
Bei späterem Ablauf/Widerruf meldet Cody fehlende aktuelle TickTick-Daten;
die sichere Autorisierung ist dann erneut erforderlich. Kein erfundener Refresh.

## Prüfung des Helfers

Die Tests verwenden ausschließlich synthetische Werte und prüfen feste
Empfänger, Read-only-Scope, Callback/Host/state, geheime stdin-Übergabe,
unterdrückte Debug-Ausgaben, Ablaufbehandlung sowie die Sperre der Secret-Ablage
bei fehlender Inbox oder anderem Scope. Der Helfer wird von der KI nicht im
geheimen Eingabemodus gestartet; die Eingabe bleibt ein notwendiger Nutzer-Handoff.
