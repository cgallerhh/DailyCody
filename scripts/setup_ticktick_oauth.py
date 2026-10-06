#!/usr/bin/env python3
"""Human-operated OAuth handoff: hidden input -> read probe -> encrypted GH secret.

No credential is written to disk, argv, environment, application logs or stdout.
Run in the user's own Terminal; do not run via a chat-controlled PTY.
"""
from __future__ import annotations

import base64
import datetime as dt
import getpass
import http.server
import json
import math
import os
from pathlib import Path
import resource
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import warnings
import webbrowser

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import ticktick_tasks

SCOPE = "tasks:read"
REPOSITORY = "github.com/cgallerhh/DailyCody"
SECRET_NAME = "TICKTICK_ACCESS_TOKEN"
CALLBACK_HOST = "127.0.0.1:8765"
REDIRECT_URI = "http://" + CALLBACK_HOST + "/callback"
TOKEN_URL = "https://ticktick.com/oauth/token"
RESULT_PATH = Path(__file__).resolve().parents[2] / "ticktick-oauth-result.json"


class SetupError(RuntimeError):
    """Only constant, redacted diagnostics may be displayed."""


def gh_environment() -> dict[str, str]:
    # Do not propagate debug/trace flags or unrelated secrets into the subprocess.
    env = {k: os.environ[k] for k in ("HOME", "PATH", "XDG_CONFIG_HOME", "GH_CONFIG_DIR") if k in os.environ}
    return {**env, "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}


def gh_command(arguments: list[str], *, secret_input: str | None = None) -> subprocess.CompletedProcess:
    executable = shutil.which("gh")
    if not executable:
        raise SetupError("GitHub CLI fehlt. Bitte die vorhandene lokale gh-Anmeldung prüfen.")
    try:
        result = subprocess.run([executable, *arguments], input=secret_input,
                                capture_output=True, text=True, timeout=60,
                                env=gh_environment())
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("GitHub-Aufruf fehlgeschlagen. Keine geheimen Details ausgegeben.") from None
    if result.returncode:
        raise SetupError("GitHub-Aufruf abgelehnt. Bitte lokale Anmeldung und Repository-Rechte prüfen.")
    return result


def check_github_destination() -> None:
    result = gh_command(["api", "repos/cgallerhh/DailyCody", "--hostname", "github.com"])
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise SetupError("GitHub-Ziel konnte nicht sicher bestätigt werden.") from None
    if data.get("full_name") != "cgallerhh/DailyCody" or not data.get("permissions", {}).get("admin"):
        raise SetupError("Das freigegebene GitHub-Ziel oder seine Secret-Berechtigung stimmt nicht.")


def authorization_url(client_id: str, state: str) -> str:
    return "https://ticktick.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "scope": SCOPE, "state": state,
        "redirect_uri": REDIRECT_URI, "response_type": "code"})


def callback_result(path: str, host: str, expected_state: str) -> str:
    if host != CALLBACK_HOST or len(path) > 8192:
        raise SetupError("Ungültige lokale OAuth-Rückgabe.")
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.path != "/callback":
        raise SetupError("Ungültiger lokaler Rückgabepfad.")
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    states = params.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], expected_state):
        raise SetupError("OAuth-state stimmt nicht überein.")
    if "error" in params:
        raise SetupError("Die TickTick-Autorisierung wurde nicht erteilt.")
    codes = params.get("code", [])
    if len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
        raise SetupError("Kein gültiger OAuth-Code empfangen.")
    return codes[0]


def receive_code(client_id: str, state: str) -> str:
    # Bind before opening authorization: another local process cannot claim our port.
    received: list[str] = []
    rejected: list[bool] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # Request paths contain the authorization code; never log them.

        def do_GET(self):
            if self.client_address[0] != "127.0.0.1" or self.headers.get("Host") != CALLBACK_HOST:
                self.send_error(400, "Unzulaessiger Host")
                return
            if self.path == "/done":
                body = b"Autorisierung empfangen. Bitte zum Terminal zurueckkehren."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            try:
                code = callback_result(self.path, self.headers.get("Host", ""), state)
            except SetupError:
                # A matching state plus provider error means the user denied consent.
                params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                states = params.get("state", [])
                if "error" in params and len(states) == 1 and secrets.compare_digest(states[0], state):
                    rejected.append(True)
                self.send_error(403, "Autorisierung nicht bestaetigt")
                return
            if not received:
                received.append(code)
            self.send_response(303)
            self.send_header("Location", "/done")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()

    class SafeServer(http.server.HTTPServer):
        def handle_error(self, request, client_address):
            pass  # No traceback from callbacks containing private OAuth codes.

    try:
        server = SafeServer(("127.0.0.1", 8765), Handler)
    except OSError:
        raise SetupError("Lokaler Port 8765 ist nicht verfügbar. Keine Autorisierung gestartet.") from None
    with server:
        server.timeout = 1
        print("Der Browser öffnet TickTick. Prüfe dein Konto und ausschließlich Lesezugriff.")
        print("Bei Schreibrechten bitte abbrechen. Keine Geheimnisse an den Chat senden.")
        if not webbrowser.open(authorization_url(client_id, state)):
            raise SetupError("Browser konnte nicht geöffnet werden. Keine Autorisierung abgeschlossen.")
        deadline = time.monotonic() + 900
        while not received and not rejected and time.monotonic() < deadline:
            server.handle_request()
        if rejected:
            raise SetupError("Die TickTick-Autorisierung wurde nicht erteilt.")
        if not received:
            raise SetupError("OAuth-Rückgabe fehlt nach 15 Minuten. Bitte erneut lokal starten.")
        # Serve the browser's redirect to the code-free /done URL without hanging.
        server.timeout = 1
        server.handle_request()
    return received[0]


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    basic = base64.b64encode((client_id + ":" + client_secret).encode()).decode()
    body = urllib.parse.urlencode({"code": code, "grant_type": "authorization_code",
                                   "scope": SCOPE, "redirect_uri": REDIRECT_URI}).encode()
    request = urllib.request.Request(TOKEN_URL, data=body, method="POST", headers={
        "Authorization": "Basic " + basic,
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        opener = urllib.request.build_opener(ticktick_tasks.NoRedirect())
        with opener.open(request, timeout=30) as response:
            payload = response.read(65537)
        if len(payload) > 65536:
            raise ValueError("oversized")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("wrong shape")
        return data
    except Exception:
        raise SetupError("TickTick-OAuth-Austausch fehlgeschlagen. Antwortinhalt wurde nicht ausgegeben.") from None


def validate_token_response(data: dict) -> tuple[str, str | None]:
    token = data.get("access_token")
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise SetupError("Kein gültiger TickTick-Access-Token erhalten.")
    if str(data.get("token_type", "Bearer")).casefold() != "bearer":
        raise SetupError("Unerwarteter TickTick-Token-Typ; nichts gespeichert.")
    # RFC 6749 §5.1: omitted scope is identical to the requested scope.
    scope = data.get("scope")
    if "scope" in data and (not isinstance(scope, str) or set(scope.split()) != {SCOPE}):
        raise SetupError("TickTick hat einen anderen Scope bestätigt. Nichts gespeichert; erneut freigeben lassen.")
    expires_at = None
    if "expires_in" in data:
        try:
            lifetime = float(data["expires_in"])
            if isinstance(data["expires_in"], bool) or not math.isfinite(lifetime) or lifetime <= 0:
                raise ValueError("invalid lifetime")
            if lifetime < 172800:
                raise SetupError("Token gilt kürzer als zwei Tage. Ohne dokumentierten Refresh keine tägliche Aktivierung.")
            expires_at = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=lifetime)).isoformat()
        except (ValueError, TypeError, OverflowError):
            raise SetupError("TickTick hat eine ungültige Token-Laufzeit geliefert; nichts gespeichert.") from None
    return token, expires_at


def publish_secret(token: str) -> None:
    gh_command(["secret", "set", SECRET_NAME, "--repo", REPOSITORY, "--app", "actions"], secret_input=token)


def save_receipt(result: ticktick_tasks.TaskRead, expires_at: str | None) -> None:
    # Only non-secret receipt metadata is persisted outside the repository.
    receipt = {"setup_complete": True, "repository": "cgallerhh/DailyCody",
               "secret_name": SECRET_NAME, "scope_requested": SCOPE,
               "local_probe_including_inbox": True, "project_count": result.project_count,
               "open_task_count": result.open_task_count, "relevant_task_count": len(result.tasks),
               "fetched_at": result.fetched_at, "expires_at": expires_at,
               "no_email_sent": True}
    tmp = RESULT_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(RESULT_PATH)


def main() -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SetupError("Bitte ausschließlich im eigenen Terminal starten; keine Chat-PTY oder umgeleitete Eingabe.")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    warnings.simplefilter("error", getpass.GetPassWarning)
    check_github_destination()
    print("Ziel: nur tasks:read; Actions-Secret in cgallerhh/DailyCody. Keine Aufgabenänderung, keine Mail.")
    print("Registrierte Redirect-URI muss exakt sein: " + REDIRECT_URI)
    client_id = getpass.getpass("TickTick Client-ID (verdeckte lokale Eingabe): ").strip()
    client_secret = getpass.getpass("TickTick Client-Secret (verdeckte lokale Eingabe): ").strip()
    if not client_id or ":" in client_id or not client_secret or any(c in client_id + client_secret for c in "\r\n"):
        raise SetupError("App-Eingaben fehlen oder sind ungültig. Nichts gespeichert.")
    code = receive_code(client_id, secrets.token_urlsafe(32))
    response = exchange_code(client_id, client_secret, code)
    token, expires_at = validate_token_response(response)
    # Do not save an unverified token. This always includes the direct Inbox GET.
    result = ticktick_tasks.read_tasks(token, dt.datetime.now(ticktick_tasks.BERLIN))
    if result.warning:
        raise SetupError(result.warning + " Kein Secret gespeichert.")
    publish_secret(token)
    try:
        save_receipt(result, expires_at)
    except OSError:
        raise SetupError("GitHub-Secret wurde gespeichert; nur der lokale Erfolgsbeleg fehlt. "
                         "Bitte diesen Status ohne Geheimnisse melden, nicht erneut autorisieren.") from None
    print(f"Erfolgreich: {result.project_count} Listen einschließlich Inbox, {result.open_task_count} offene Aufgaben.")
    print("Token direkt verschlüsselt als GitHub-Actions-Secret gespeichert; keine Mail versendet.")
    print("Token-Ablauf: " + (expires_at or "vom Anbieter nicht angegeben; Ablauf/Widerruf wird sichtbar gemeldet."))
    print("Bitte im Chat nur 'fertig' melden. Anschließend folgt die Prüfung im echten Actions-Runner.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SetupError as exc:
        print(str(exc), file=sys.stderr)  # All SetupError messages are redacted.
        raise SystemExit(1)
    except (Exception, KeyboardInterrupt):
        # Never render unexpected exception objects, response JSON or a traceback.
        print("Einrichtung nicht vollständig abgeschlossen. Bitte lokal erneut starten oder ohne geheime Angaben melden.", file=sys.stderr)
        raise SystemExit(1)
