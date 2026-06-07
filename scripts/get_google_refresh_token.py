#!/usr/bin/env python3
"""One-time helper to create a Google refresh token for GitHub Secrets."""

from __future__ import annotations

import json
import http.server
import secrets
import threading
import urllib.parse
import urllib.request
import webbrowser


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/tasks.readonly",
]


def main() -> None:
    client_id = input("Google OAuth Client ID: ").strip()
    client_secret = input("Google OAuth Client Secret: ").strip()
    redirect_uri = "http://localhost:8765/callback"
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/callback" and params.get("state", [""])[0] == state:
                result["code"] = params.get("code", [""])[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Daily Cody is authorized. You can close this tab.")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Authorization failed.")

        def log_message(self, format: str, *args: object) -> None:
            return

    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    server = http.server.HTTPServer(("localhost", 8765), CallbackHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    print("\nOpening Google authorization in your browser...\n")
    webbrowser.open(url)
    thread.join(timeout=180)
    server.server_close()
    code = result.get("code")
    if not code:
        raise RuntimeError("No authorization code received. Try again and finish within 3 minutes.")
    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    print("\nAdd these GitHub Secrets:")
    print(f"GOOGLE_CLIENT_ID={client_id}")
    print(f"GOOGLE_CLIENT_SECRET={client_secret}")
    print(f"GOOGLE_REFRESH_TOKEN={payload['refresh_token']}")


if __name__ == "__main__":
    main()
