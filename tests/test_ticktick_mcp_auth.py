import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.parse
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import ticktick_mcp_auth as a
import ticktick_mcp as m
import ticktick_tasks as tt

TOKEN = "synthetic-private-new-mcp-access"
REFRESH = "synthetic-private-new-mcp-refresh"
CODE = "synthetic-private-code"


class MemoryStore:
    writable = True
    def __init__(self, state):
        self.state = state
        self.saved = []
    def load(self):
        return self.state
    def save(self, state):
        self.state = state
        self.saved.append(state)


def state(**fields):
    return a.State("synthetic-own-client", TOKEN, REFRESH, expires_at=1000, **fields)


class MCPAuthTest(unittest.TestCase):
    def test_public_discovery_binding_pkce_and_no_claim_about_unadvertised_refresh(self):
        resource = {"resource": a.RESOURCE, "authorization_servers": [a.ISSUER + "/"],
                    "scopes_supported": ["tasks:write", "tasks:read"], "bearer_methods_supported": ["header"]}
        server = {"issuer": a.ISSUER, "authorization_endpoint": a.AUTHORIZE, "token_endpoint": a.TOKEN_URL,
                  "registration_endpoint": a.REGISTER_URL, "code_challenge_methods_supported": ["S256"],
                  "grant_types_supported": ["authorization_code"], "token_endpoint_auth_methods_supported": ["none"]}
        a.validate_discovery(resource, server)  # Refresh omission alone is not impossibility proof.
        for key in ("issuer", "token_endpoint", "registration_endpoint"):
            bad = {**server, key: "https://evil.invalid/"}
            with self.subTest(key=key), self.assertRaises(a.AuthError):
                a.validate_discovery(resource, bad)
        with self.assertRaises(a.AuthError):
            a.validate_discovery({**resource, "resource": "https://evil.invalid/"}, server)
        with self.assertRaises(a.AuthError):
            a.validate_discovery(resource, {**server, "code_challenge_methods_supported": ["plain"]})

    def test_public_registration_payload_and_authorization_only_read_pkce_resource(self):
        payload = a.registration_payload(request_refresh=True)
        self.assertEqual(payload["scope"], "tasks:read")
        self.assertEqual(payload["token_endpoint_auth_method"], "none")
        self.assertEqual(payload["redirect_uris"], ["http://127.0.0.1:8766/callback"])
        verifier, challenge = a.pkce()
        self.assertGreaterEqual(len(verifier), 43)
        url = a.authorize_url("synthetic-own-client", "synthetic-state", challenge)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(params["scope"], ["tasks:read"])
        self.assertEqual(params["resource"], [a.RESOURCE])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertNotIn(verifier, url)
        self.assertNotIn(REFRESH, url)

    def test_exchange_fixed_form_pkce_and_scope_rejects_expansion(self):
        with patch.object(a, "token_post", return_value={"access_token": TOKEN, "expires_in": 3600, "refresh_token": REFRESH, "scope": "tasks:read"}) as post:
            value = a.exchange("synthetic-own-client", CODE, "synthetic-verifier", now=100)
        self.assertEqual(value.expires_at, 3700)
        form = post.call_args.args[0]
        self.assertEqual(form["scope"], "tasks:read")
        self.assertEqual(form["resource"], a.RESOURCE)
        self.assertEqual(form["code_verifier"], "synthetic-verifier")
        for scope in ("tasks:read tasks:write", "tasks:write", None, []):
            with self.subTest(scope=scope), self.assertRaises(a.AuthError):
                a.state_from_response({"access_token": TOKEN, "expires_in": 3600, "scope": scope}, "c", now=100)

    def test_missing_expiry_nonfinite_or_invalid_tokens_block_lifecycle(self):
        for ttl in (None, 0, -1, True, float("inf"), "3600"):
            with self.subTest(ttl=ttl), self.assertRaises(a.AuthError):
                a.state_from_response({"access_token": TOKEN, "expires_in": ttl}, "c", now=100)
        for token in (None, "", "bad\nheader"):
            with self.subTest(token=token), self.assertRaises(a.AuthError):
                a.state_from_response({"access_token": token, "expires_in": 3600}, "c", now=100)

    def test_expiry_boundary_refuses_refresh_without_writable_store_before_network(self):
        store = a.EnvironmentStore(state().to_json())
        with patch.object(a, "token_post") as post:
            self.assertEqual(a.Lifecycle(store).access_token(now=939), TOKEN)
            with self.assertRaises(a.AuthError):
                a.Lifecycle(store).access_token(now=940)
            with self.assertRaises(a.AuthError):
                a.Lifecycle(store).access_token(now=100, force_refresh=True)
        post.assert_not_called()

    def test_forced_refresh_rotates_saves_then_restarts_from_new_state(self):
        store = MemoryStore(state())
        reply = {"access_token": "synthetic-next-access", "refresh_token": "synthetic-next-refresh", "expires_in": 3600, "scope": "tasks:read"}
        with patch.object(a, "token_post", return_value=reply) as post:
            self.assertEqual(a.Lifecycle(store).access_token(now=100, force_refresh=True), reply["access_token"])
            self.assertEqual(a.Lifecycle(store).access_token(now=101), reply["access_token"])
        self.assertEqual(store.state.generation, 1)
        self.assertEqual(store.state.refresh_token, reply["refresh_token"])
        self.assertEqual(store.saved, [store.state])
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0]["scope"], "tasks:read")
        self.assertEqual(post.call_args.args[0]["resource"], a.RESOURCE)
        # Fresh process simulation: only the securely saved state may be used.
        restarted = a.EnvironmentStore(store.state.to_json())
        self.assertEqual(a.Lifecycle(restarted).access_token(now=102), reply["access_token"])

    def test_storage_failure_or_stale_reload_never_releases_new_access_token(self):
        for stale in (False, True):
            store = MemoryStore(state())
            if stale:
                store.save = Mock()
            else:
                store.save = Mock(side_effect=OSError(TOKEN + REFRESH))
            with patch.object(a, "token_post", return_value={"access_token": "synthetic-next", "expires_in": 3600}), self.assertRaises(a.AuthError) as error:
                a.Lifecycle(store).access_token(now=100, force_refresh=True)
            for secret in (TOKEN, REFRESH, "synthetic-next"):
                self.assertNotIn(secret, str(error.exception))

    def test_refresh_denial_scope_change_and_missing_refresh_never_save(self):
        for reply in [{"access_token": TOKEN, "expires_in": 3600, "scope": "tasks:write"},
                      {"access_token": TOKEN, "expires_in": 3600, "refresh_token": None}]:
            store = MemoryStore(state())
            with patch.object(a, "token_post", return_value=reply), self.assertRaises(a.AuthError):
                a.Lifecycle(store).access_token(now=100, force_refresh=True)
            self.assertEqual(store.saved, [])
        store = MemoryStore(a.State("c", TOKEN, expires_at=1000))
        with patch.object(a, "token_post") as post, self.assertRaises(a.AuthError):
            a.Lifecycle(store).access_token(now=100, force_refresh=True)
        post.assert_not_called()

    def test_state_binding_repr_snapshot_and_no_other_credentials_accepted(self):
        value = state()
        self.assertNotIn(TOKEN, repr(value))
        self.assertNotIn(REFRESH, repr(value))
        self.assertNotIn(REFRESH, value.access_snapshot().to_json())
        self.assertIsNone(value.access_snapshot().refresh_token)
        self.assertEqual(a.State.from_json(value.to_json()), value)
        for changes in [{"scope": "tasks:write"}, {"resource": "https://api.ticktick.com/"}, {"purpose": "chatgpt-connector"}]:
            data = {**value.__dict__, **changes}
            with self.subTest(changes=changes), self.assertRaises(a.AuthError):
                a.State.from_json(json.dumps(data))
        with self.assertRaises(a.AuthError):
            a.State.from_json(TOKEN)

    def test_oauth_network_destination_no_redirect_and_private_errors_redacted(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"expires_in":3600}'
        opener = Mock()
        opener.open.return_value = response
        with patch.object(a.urllib.request, "build_opener", return_value=opener) as factory:
            self.assertEqual(a.token_post({"refresh_token": REFRESH}), {"expires_in": 3600})
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.ticktick.com/oauth/token")
        self.assertEqual(request.get_method(), "POST")
        self.assertIsInstance(factory.call_args.args[0], tt.NoRedirect)
        opener.open.side_effect = RuntimeError(TOKEN + REFRESH + CODE)
        with patch.object(a.urllib.request, "build_opener", return_value=opener), self.assertRaises(a.AuthError) as error:
            a.token_post({})
        self.assertNotIn(TOKEN, str(error.exception))


class MCPLifecycleHarnessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("mcp_harness", ROOT / "scripts/check_ticktick_mcp_lifecycle.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_read_refresh_and_restart_receipts_contain_only_metadata(self):
        store = MemoryStore(state())
        result = tt.TaskRead(inbox_probe_verified=True, project_count=4, open_task_count=12)
        with patch.object(m, "TaskReader") as reader, patch.object(a, "token_post", return_value={"access_token": "synthetic-next", "expires_in": 3600, "refresh_token": "synthetic-next-refresh"}):
            reader.return_value.read.return_value = result
            first = self.module.check(store, "synthetic-probe", now=100)
            renewed = self.module.check(store, "synthetic-probe", now=100, force_refresh=True, expected_generation=1)
            restarted = self.module.check(a.EnvironmentStore(store.state.to_json()), "synthetic-probe", now=101, expected_generation=1)
        self.assertFalse(first["force_refresh_passed"])
        self.assertTrue(renewed["force_refresh_passed"])
        self.assertTrue(restarted["inbox_id_probe_passed"])
        for private in (TOKEN, REFRESH, "synthetic-probe", "synthetic-next"):
            self.assertNotIn(private, json.dumps([first, renewed, restarted]))
        self.assertTrue(all(not row["email_sent"] for row in (first, renewed, restarted)))

    def test_missing_probe_stale_generation_or_empty_inbox_cannot_pass(self):
        store = MemoryStore(state())
        with patch.object(m, "TaskReader") as reader:
            reader.return_value.read.return_value = tt.TaskRead(inbox_probe_verified=False)
            for kwargs in [{"expected_id": ""}, {"expected_id": "probe", "expected_generation": 99}, {"expected_id": "probe"}]:
                with self.subTest(kwargs=kwargs), self.assertRaises(a.AuthError):
                    self.module.check(store, now=100, **kwargs)
