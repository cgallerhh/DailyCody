"""Separate Cody MCP OAuth contracts and rotation-safe lifecycle, no token lookup.

This module performs no registration or authorization at import. Durable storage
is injected; environment-only runners cannot consume a rotating refresh token.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import secrets
import time
import urllib.parse
import urllib.request

from ticktick_tasks import NoRedirect

RESOURCE = "https://mcp.ticktick.com/"
ISSUER = "https://ticktick.com"
AUTHORIZE = ISSUER + "/oauth/authorize"
TOKEN_URL = "https://api.ticktick.com/oauth/token"
REGISTER_URL = "https://api.ticktick.com/oauth/register"
REDIRECT = "http://127.0.0.1:8766/callback"
SCOPE = "tasks:read"
PURPOSE = "daily-cody.ticktick-mcp.v1"


class AuthError(RuntimeError):
    """Redacted OAuth or storage failure."""


def validate_discovery(resource: dict, server: dict) -> None:
    if not isinstance(resource, dict) or not isinstance(server, dict):
        raise AuthError("TickTick-MCP-OAuth-Discovery ist ungültig.")
    for source, fields in [(resource, ("authorization_servers", "scopes_supported", "bearer_methods_supported")),
                           (server, ("code_challenge_methods_supported", "grant_types_supported", "token_endpoint_auth_methods_supported"))]:
        if any(not isinstance(source.get(key), list) for key in fields):
            raise AuthError("TickTick-MCP-OAuth-Discovery ist ungültig.")
    if (resource.get("resource") != RESOURCE or resource.get("authorization_servers") != [ISSUER + "/"] or
            SCOPE not in resource.get("scopes_supported", []) or "header" not in resource.get("bearer_methods_supported", [])):
        raise AuthError("TickTick-MCP-Resource-Discovery ist nicht freigegeben.")
    if (server.get("issuer") != ISSUER or server.get("authorization_endpoint") != AUTHORIZE or
            server.get("token_endpoint") != TOKEN_URL or server.get("registration_endpoint") != REGISTER_URL or
            "S256" not in server.get("code_challenge_methods_supported", []) or
            "authorization_code" not in server.get("grant_types_supported", []) or
            "none" not in server.get("token_endpoint_auth_methods_supported", [])):
        raise AuthError("TickTick-MCP-OAuth-Discovery ist nicht kompatibel.")


def registration_payload(*, request_refresh: bool = False) -> dict:
    # A requested refresh grant is a lifecycle capability, never a broader scope.
    # Its acceptance must be checked before showing the user another login page.
    return {"client_name": "Daily Cody read-only MCP", "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none", "scope": SCOPE,
            "grant_types": ["authorization_code"] + (["refresh_token"] if request_refresh else []),
            "response_types": ["code"]}


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
    return verifier, challenge


def authorize_url(client_id: str, state: str, challenge: str) -> str:
    return AUTHORIZE + "?" + urllib.parse.urlencode({"client_id": client_id, "redirect_uri": REDIRECT,
        "response_type": "code", "scope": SCOPE, "state": state, "resource": RESOURCE,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def token_post(form: dict) -> dict:
    request = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            body = response.read(65537)
        if len(body) > 65536:
            raise ValueError("oversized")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("shape")
        return data
    except Exception:
        raise AuthError("TickTick-MCP-OAuth-Aufruf fehlgeschlagen; keine Antwortinhalte ausgegeben.") from None


@dataclass(frozen=True)
class State:
    client_id: str = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: float = 0
    generation: int = 0
    scope: str = SCOPE
    resource: str = RESOURCE
    purpose: str = PURPOSE

    def __post_init__(self):
        if (self.scope != SCOPE or self.resource != RESOURCE or self.purpose != PURPOSE or
                not isinstance(self.client_id, str) or not self.client_id or
                not isinstance(self.access_token, str) or not self.access_token or
                any(c.isspace() for c in self.access_token) or
                (self.refresh_token is not None and (not isinstance(self.refresh_token, str) or not self.refresh_token or any(c.isspace() for c in self.refresh_token))) or
                type(self.expires_at) not in (int, float) or not math.isfinite(self.expires_at) or self.expires_at <= 0 or
                type(self.generation) is not int or self.generation < 0):
            raise AuthError("Separater Cody-MCP-Tokenzustand ist ungültig oder nicht ausschließlich lesend.")

    @classmethod
    def from_json(cls, value: str):
        try:
            if not isinstance(value, str) or len(value) > 65536:
                raise ValueError("size")
            data = json.loads(value)
            if not isinstance(data, dict) or data.get("purpose") != PURPOSE or data.get("scope") != SCOPE or data.get("resource") != RESOURCE:
                raise ValueError("binding")
            return cls(**data)
        except (ValueError, TypeError):
            raise AuthError("TickTick-MCP-Zugriff ist nicht eingerichtet oder sein Zustand ist ungültig.") from None

    def to_json(self) -> str:
        # Secret data for an approved encrypted store only; never print this value.
        return json.dumps(self.__dict__, sort_keys=True)

    def access_snapshot(self) -> "State":
        """Minimal Actions credential: refresh token stays in approved keeper."""
        return replace(self, refresh_token=None)


def state_from_response(data: dict, client_id: str, *, now: float, prior: State | None = None) -> State:
    if "scope" in data and (not isinstance(data["scope"], str) or set(data["scope"].split()) != {SCOPE}):
        raise AuthError("TickTick-MCP hat einen anderen Scope bestätigt; Einrichtung bleibt gesperrt.")
    if str(data.get("token_type", "Bearer")).lower() != "bearer":
        raise AuthError("TickTick-MCP-Token-Typ ist nicht unterstützt.")
    ttl = data.get("expires_in")
    if type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl <= 0:
        raise AuthError("TickTick-MCP-Tokenablauf ist nicht bestätigt; keine automatische Aktivierung.")
    refresh = data.get("refresh_token", prior.refresh_token if prior else None)
    return State(client_id=client_id, access_token=data.get("access_token"), refresh_token=refresh,
                 expires_at=now + ttl, generation=(prior.generation + 1 if prior else 0))


def exchange(client_id: str, code: str, verifier: str, *, now: float | None = None) -> State:
    data = token_post({"grant_type": "authorization_code", "client_id": client_id, "code": code,
        "code_verifier": verifier, "redirect_uri": REDIRECT, "scope": SCOPE, "resource": RESOURCE})
    return state_from_response(data, client_id, now=time.time() if now is None else now)


class EnvironmentStore:
    """Initial encrypted Actions secret is readable, but cannot persist rotation."""
    writable = False

    def __init__(self, value: str):
        self._value = value

    def load(self) -> State:
        return State.from_json(self._value)


class Lifecycle:
    def __init__(self, store):
        self.store = store

    def access_token(self, *, now: float | None = None, force_refresh: bool = False) -> str:
        now = time.time() if now is None else now
        state = self.store.load()
        if not force_refresh and state.expires_at > now + 60:
            return state.access_token
        if not state.refresh_token:
            raise AuthError("TickTick-MCP-Token benötigt Erneuerung; Refresh ist nicht bestätigt.")
        if not getattr(self.store, "writable", False):
            # Check BEFORE spending a possibly single-use rotating refresh token.
            raise AuthError("TickTick-MCP-Refresh blockiert: dauerhafter sicherer Zustandspeicher fehlt.")
        data = token_post({"grant_type": "refresh_token", "refresh_token": state.refresh_token,
            "client_id": state.client_id, "scope": SCOPE, "resource": RESOURCE})
        updated = state_from_response(data, state.client_id, now=now, prior=state)
        if not updated.refresh_token:
            raise AuthError("TickTick-MCP-Refresh hat keinen weiter erneuerbaren Zustand bestätigt.")
        try:
            self.store.save(updated)
            durable = self.store.load()
            if durable != updated:
                raise ValueError("durability")
        except Exception:
            raise AuthError("TickTick-MCP-Refreshzustand konnte nicht sicher bestätigt werden; kein weiterer Abruf.") from None
        return updated.access_token
