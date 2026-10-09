#!/usr/bin/env python3
"""Prepared human-only MCP handoff. Paused until the revised plan is approved."""
import http.server
import json
import os
from pathlib import Path
import resource
import secrets
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import ticktick_mcp_auth as auth
from ticktick_mcp_store import KeychainStore, MissingState
from ticktick_tasks import NoRedirect
import check_ticktick_mcp_lifecycle as harness
from setup_ticktick_oauth import check_github_destination, gh_command

HANDOFF_APPROVED = False  # Not an environment/CLI override or implied by CI.
HELPER = ROOT / ".build/ticktick-mcp-keychain"
RECEIPT = ROOT.parent / "ticktick-mcp-result.json"


def public_json(url, payload=None):
    allowed = {"https://mcp.ticktick.com/.well-known/oauth-protected-resource",
               "https://ticktick.com/.well-known/oauth-authorization-server", auth.REGISTER_URL}
    if url not in allowed or (payload is not None and url != auth.REGISTER_URL):
        raise auth.AuthError("Nicht freigegebener Discovery-/Registrierungsempfänger.")
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                    headers={"Accept": "application/json", "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ValueError("size")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("shape")
        return result
    except Exception:
        raise auth.AuthError("MCP-Discovery oder Registrierung fehlgeschlagen; keine Antwortinhalte ausgegeben.") from None


def register():
    metadata = public_json("https://mcp.ticktick.com/.well-known/oauth-protected-resource")
    server = public_json("https://ticktick.com/.well-known/oauth-authorization-server")
    auth.validate_discovery(metadata, server)
    reply = public_json(auth.REGISTER_URL, auth.registration_payload(request_refresh=True))
    if (not isinstance(reply.get("client_id"), str) or not reply["client_id"] or
            reply.get("token_endpoint_auth_method") != "none" or reply.get("redirect_uris") != [auth.REDIRECT] or
            set(reply.get("grant_types", [])) != {"authorization_code", "refresh_token"} or
            ("scope" in reply and reply["scope"] != auth.SCOPE) or reply.get("client_secret")):
        raise auth.AuthError("Registrierung bestätigt Public-Client/Read-only/Refresh nicht; kein Browser-Login gestartet.")
    return reply["client_id"]


def consent(client_id):
    state = secrets.token_urlsafe(32)
    verifier, challenge = auth.pkce()
    received = []
    denied = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def do_GET(self):
            if self.client_address[0] != "127.0.0.1" or self.headers.get("Host") != "127.0.0.1:8766":
                self.send_error(400)
                return
            if self.path == "/done":
                self.send_response(200)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b"Autorisierung empfangen. Bitte zum Terminal zurueckkehren.")
                return
            parsed = urllib.parse.urlsplit(self.path)
            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            valid = (len(self.path) <= 8192 and not parsed.netloc and not parsed.scheme and parsed.path == "/callback" and
                     len(params.get("state", [])) == 1 and secrets.compare_digest(params["state"][0], state))
            if valid and "error" in params:
                denied.append(True)
            codes = params.get("code", [])
            if not valid or "error" in params or len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
                self.send_error(403)
                return
            if not received:
                received.append(codes[0])
            self.send_response(303)
            self.send_header("Location", "/done")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
    class Server(http.server.HTTPServer):
        def handle_error(self, *args):
            pass
    try:
        listener = Server(("127.0.0.1", 8766), Handler)
    except OSError:
        raise auth.AuthError("Lokaler MCP-Callback-Port ist nicht verfügbar; keine Anmeldung gestartet.") from None
    with listener:
        listener.timeout = 1
        print("Im Browser selbst christian.galler@gmail.com und ausschließlich tasks:read prüfen.")
        if not webbrowser.open(auth.authorize_url(client_id, state, challenge)):
            raise auth.AuthError("Browser konnte nicht geöffnet werden.")
        deadline = time.monotonic() + 900
        while not received and not denied and time.monotonic() < deadline:
            listener.handle_request()
        if not received or denied:
            raise auth.AuthError("MCP-OAuth-Zustimmung fehlt; keine geheimen Details ausgegeben.")
        listener.handle_request()  # Serve the code-free /done redirect once.
    return auth.exchange(client_id, received[0], verifier)


def publish(state, expected_id):
    if state.expires_at <= time.time() + 60:
        raise auth.AuthError("Actions-Veröffentlichung blockiert: eigener MCP-Zugriff bereits erneuerungsbedürftig.")
    gh_command(["secret", "set", "TICKTICK_MCP_AUTH_JSON", "--repo", "github.com/cgallerhh/DailyCody", "--app", "actions"],
               secret_input=state.access_snapshot().to_json())
    gh_command(["secret", "set", "TICKTICK_MCP_INBOX_PROBE_ID", "--repo", "github.com/cgallerhh/DailyCody", "--app", "actions"],
               secret_input=expected_id)


def main():
    if not HANDOFF_APPROVED:
        raise auth.AuthError("MCP-Handoff noch nicht abgestimmt. Keine Anmeldung, Registrierung, Keychain- oder Secret-Einrichtung.")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise auth.AuthError("Bitte ausschließlich im eigenen Terminal starten, nicht über Chat-PTY.")
    expected_id = os.getenv("TICKTICK_MCP_INBOX_PROBE_ID", "")
    if not expected_id:
        raise auth.AuthError("Bekannte undatierte Inbox-Aufgabe nach ID fehlt; keine Testaufgabe ohne Freigabe anlegen.")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if not HELPER.is_file():
        raise auth.AuthError("Geprüfter eigener Keychain-Helfer ist vor dem Login bereitzustellen.")
    check_github_destination()
    store = KeychainStore(HELPER)
    with store.transaction():
        try:
            store.load()  # Only this new dedicated Cody item, never other clients.
        except MissingState:
            client_id = register()  # Once, after explicit revised handoff approval.
            value = consent(client_id)
            if not value.refresh_token:
                raise auth.AuthError("MCP lieferte keinen Refresh-Token; keine unbeaufsichtigte Aktivierung.")
            store.save(value)  # Approved encrypted seed; prevents repeated sign-in on probe failures.
        initial = harness.check(store, expected_id)
        renewed = harness.check(store, expected_id, force_refresh=True)
        # Each Keychain load runs a separate native helper process.
        restarted = harness.check(KeychainStore(HELPER), expected_id, expected_generation=renewed["generation"])
        publish(store.load(), expected_id)
        receipt = {"initial": initial, "refresh": renewed, "restart": restarted,
                   "actions_test_still_required": True, "mail_sent": False}
        tmp = RECEIPT.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
        tmp.replace(RECEIPT)
    print("MCP-Lese-, Refresh- und Keychain-Neustartprobe erfolgreich. Actions-Secret enthält keinen Refresh-Token.")
    print("Keine Mail versendet. Bitte nur 'fertig' melden; Actions-Livecheck bleibt erforderlich.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, (auth.AuthError, harness.ticktick_mcp.MCPError, harness.tt.TickTickError)):
            print(str(exc), file=sys.stderr)
        else:
            print("MCP-Handoff nicht abgeschlossen; keine geheimen Details ausgegeben. Eigenen gespeicherten Zustand behalten, nicht erneut anmelden.", file=sys.stderr)
        raise SystemExit(1)
